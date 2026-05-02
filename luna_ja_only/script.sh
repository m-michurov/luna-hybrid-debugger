#!/bin/bash

peers="$(grep -vc "^$" "$1")"
export COMM_SIZE=$peers
export COMM_CONFIG=$1

for ((i=0; i<"$peers"; i++))
do
  export COMM_RANK=$i
  "${@:2}" &
done
