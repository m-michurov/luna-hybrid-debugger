# luna-assert-generator

## Зависимости

 * libclang 18
```bash
sudo apt update
sudo apt install -y libclang-dev python3-clang
sudo wget -qO- https://apt.llvm.org/llvm.sh | sudo bash -s -- 18
cd /usr/lib/llvm-18/lib/
sudo ln -s libclang-18.so.1 libclang.so
```
 * [requirements.txt](requirements.txt)
```bash
pip install requirements.txt
```