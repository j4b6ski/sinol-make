#!/bin/bash

# This script generates input tests in parallel.
# Test names are read from file "tests" in the same directory split by spaces.

function generate() {
    start=$1
    end=$2
    for ((i=start; i<end; i++)); do
      ../cache/gen "${tests[$i]}" > "../in/${tests[$i]}"
      echo "Generated ${tests[$i]}"
    done
}

cd "$(dirname "$0")" || exit 1
mkdir -p ../cache
g++ gen_helper.cpp -o ../cache/gen

cpu_num=$(nproc)

IFS=' '
read -a tests <<< "$(cat tests)"
len=${#tests[@]}

# Strip file names
for ((i=0; i<len; i++)); do
    tests[$i]=$(echo "${tests[$i]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
done

increment=$((len/cpu_num))
if [ $increment -eq 0 ]; then
    increment=1
fi

finish=0
for ((i=0; i<cpu_num; i++)); do
    start=$((i*increment))
    end=$((start+increment))
    if [ "$i" -eq $((cpu_num-1)) ]; then
        end=$len
    fi
    if [ "$end" -ge "$len" ]; then
        end=$len
        finish=1
    fi
    generate $start "$end" &
    pids[${i}]=$!
    if [ "$finish" -eq 1 ]; then
        break
    fi
done

for pid in ${pids[*]}; do
    wait $pid
done
