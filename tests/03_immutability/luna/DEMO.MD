```
(.venv) ~/Projects/luna-assert-generator git:[master]
python3 instrument_cpp.py tests/03_immutability/luna/ucodes.cpp
Generated 15 hash checks.
Hash checks inserted into ucodes_instrumented.cpp.
(.venv) ~/Projects/luna-assert-generator git:[master]
luna  tests/03_immutability/luna/jacobi.fa 100 100 2 2
eps: 0.000000010
luna: fatal error: run-time error: errcode=-6
err> Assertion failed: File ucodes.cpp, function update_subgrid_no_edges: input parameter prev_df changed
err> Assertion failed: File ucodes.cpp, function update_subgrid_no_edges: input parameter prev_df changed
err> Assertion failed: File ucodes.cpp, function update_subgrid_no_edges: input parameter prev_df changed
err> Assertion failed: File ucodes.cpp, function update_subgrid_no_edges: input parameter prev_df changed
err> terminate called recursively
err> terminate called after throwing an instance of 'std::runtime_error'
err> terminate called after throwing an instance of 'std::runtime_error'
err>   what():  [MS-7B53:40396] *** Process received signal ***
err> generated assertion failed  what():  generated assertion failed
err> [MS-7B53:40396] Signal: Aborted (6)
err> [MS-7B53:40396] Signal code:  (-6)
err> terminate called after throwing an instance of 'std::runtime_error'
err> 
err>   what():  generated assertion failed
err> 
```