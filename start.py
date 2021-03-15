from subprocess import DEVNULL
import os
import gc
import sys
import time
import datetime
import pandas as pd
from multiprocessing import Pool
import subprocess as sp
import shutil
from zipfile import ZipFile
from os.path import basename

SV_DIR_BASE = "/AFLsymcc/sv-benchmarks/c/"
SYMCC_BIN = "/AFLsymcc/symcc/build_qsym/symcc"

TIME_START = time.time()


def intersection(lst1, lst2): 
    return list(set(lst1) & set(lst2))

def files(path):
    for file in os.listdir(path):
        if os.path.isfile(os.path.join(path, file)):
            yield file

def delete_folder(folder):
    if os.path.exists(folder) and os.path.isdir(folder):
        shutil.rmtree(folder)

def get_tasks(category):
    tasks = pd.read_csv("name_category.csv", header = 0)
    tasks = tasks[tasks["category"] == category]
    return [SV_DIR_BASE + task.replace("yml", "c") for task in tasks["sv-benchmarks"].values.tolist()]


def save_test_to_file(task, time_stamp, data, suffix):
    os.system("mkdir -p output/{}".format(task.split("/")[-1]))
    with open('output/{}/{}_{}.xml'.format(task.split("/")[-1], format(time_stamp, ">09.4f"), suffix), 'wt+') as input_file:
        input_file.write(
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n')
        input_file.write(
            '<!DOCTYPE testcase PUBLIC "+//IDN sosy-lab.org//DTD test-format testcase 1.1//EN" "https://sosy-lab.org/test-format/testcase-1.1.dtd">\n')
        input_file.write('<testcase>\n')
        input_file.write(data)
        input_file.write('</testcase>\n')


def binary_execute_parallel(task, input_bytes):
    binary = "./bin/{}".format(task.split("/")[-1])
    instr = sp.Popen(binary, stdin=sp.PIPE, stdout=sp.PIPE,
                         stderr=sp.PIPE, close_fds=True)
    msg = ret = None
    timeout = False
    start_conex = time.time()
    try:
        msg = instr.communicate(input_bytes[0])
        ret = instr.returncode
        instr.terminate()
        del instr
        gc.collect()
    except:
        pass
    curr_time = time.time() - TIME_START
    save_test_to_file(task, curr_time, msg[0].decode('utf-8'), ("-C") + ("-" + input_bytes[1]))


def afl_simulation(task, new_paths):
    # method = "A"
    results = []
    for path in intersection(files("work_dir/{}/output/default/queue".format(task.split("/")[-1])), new_paths):
        with open("work_dir/{}/output/default/queue/".format(task.split("/")[-1]) + path, "rb") as f:
            if "op:symcc-mutator.so" in path:
                method = "D"
            else:
                method = "A"
            byte = f.read()
            result = (byte, method)
        results.append(result)
    test = [binary_execute_parallel(task, mutant) for mutant in results]


def start_symcc(task):

    # mkdir
    os.system("mkdir -p bin")
    os.system("mkdir -p work_dir/{}/input".format(task.split("/")[-1]))
    os.system("mkdir -p work_dir/{}/output".format(task.split("/")[-1]))

    # Seed input
    f = open("work_dir/{}/input/seed".format(task.split("/")[-1]), "w")
    f.write("seed")
    f.close()
    
    # Create metadata
    os.system("mkdir -p output/{}".format(task.split("/")[-1]))
    with open("output/{}/metadata.xml".format(task.split("/")[-1]), "wt+") as md:
        md.write('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n')
        md.write(
            '<!DOCTYPE test-metadata PUBLIC "+//IDN sosy-lab.org//DTD test-format test-metadata 1.1//EN" "https://sosy-lab.org/test-format/test-metadata-1.1.dtd">\n')
        md.write('<test-metadata>\n')
        md.write('<sourcecodelang>C</sourcecodelang>\n')
        md.write('<producer>Legion</producer>\n')
        md.write('<specification>CHECK( LTL(G ! call(__VERIFIER_error())) )</specification>\n')
        md.write('<programfile>{}</programfile>\n'.format(task))
        res = sp.run(["sha256sum", task], stdout=sp.PIPE)
        out = res.stdout.decode('utf-8')
        sha256sum = out[:64]
        md.write('<programhash>{}</programhash>\n'.format(sha256sum))
        md.write('<entryfunction>main</entryfunction>\n')
        md.write('<architecture>32bit</architecture>\n')
        md.write('<creationtime>{}</creationtime>\n'.format(datetime.datetime.now()))
        md.write('</test-metadata>\n')


    binary = "./bin/{}".format(task.split("/")[-1])

    # gcc compile
    print("======== gcc compile =========")
    sp.run(["gcc", "__VERIFIER.c", "__VERIFIER_assume.c", task, "-o", binary], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    # afl instrumentation
    print("======== afl compile =========")
    sp.run(["afl-cc", "__VERIFIER.c", "__VERIFIER_assume.c", task, "-o", binary + "_afl"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    # symcc instrumentation
    print("======== symcc compile =========")
    sp.run([SYMCC_BIN, "__VERIFIER.c", "__VERIFIER_assume.c", task, "-o", binary + "_symcc"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    
    print("======== start afl-symcc =========")
    cmd = "SYMCC_TARGET={}_symcc AFL_CUSTOM_MUTATOR_LIBRARY=/AFLsymcc/AFLplusplus/custom_mutators/symcc/symcc-mutator.so timeout 15 afl-fuzz -i work_dir/{}/input -o work_dir/{}/output {}_afl".format(binary, task.split("/")[-1], task.split("/")[-1], binary)
    p = sp.Popen(cmd, shell=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    AFL_DIR = dict ()
    global TIME_START
    TIME_START = time.time()
    time.sleep(5)
    while True:
        poll = p.poll()
        if poll is not None:
            print(task)
            print("timeout!")
            break
        added = False
        try:
            after = dict ([(f, None) for f in os.listdir ("work_dir/{}/output/default/queue".format(task.split("/")[-1]))])
            added = [f for f in after if not f in AFL_DIR]
        except:
            added = False
        if (added):
            # print("afl added")
            AFL_DIR = after
            afl_simulation(task, new_paths=added)


def make_zip():
    folders = next(os.walk('output'))[1]
    for dir_name in folders:
        os.system("mkdir -p output_zip/{}".format(dir_name[:-2] + ".yml"))
        with ZipFile("output_zip/{}/test-suite.zip".format(dir_name[:-2] + ".yml"), 'w') as zipObj:
            for folderName, subfolders, filenames in os.walk("output/{}/".format(dir_name)):
                for filename in sorted(filenames):
                    print(filename)
                    filePath = os.path.join(folderName, filename)
                    zipObj.write(filePath, basename(filePath))

if __name__ == '__main__':
    delete_folder("./work_dir")
    delete_folder("./output")
    os.environ['AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES'] = "1"
    os.environ['AFL_DONT_OPTIMIZE'] = "1"
    p = Pool(processes = 14)
    tasks = get_tasks("ReachSafety-ControlFlow")
    async_result = p.map_async(start_symcc, tasks)
    p.close()
    p.join()
    make_zip()