# 项目环境配置

## 使用uv包管理器

```
pip install uv --upgrade    # 下载uv
uv venv --python=3.13       # 创建虚拟环境

# 激活虚拟环境
Mac OS | Linux : source .venv/bin/activate
Windows : .venv\Scripts\activate

# 同步依赖环境
uv sync

# 检查torch.cuda是否可用（如有）
python
import torch
print(torch.__version__)
print(torch.cuda.is_available())

# 若torch版本为2.9.0+cpu，cuda.is_available=False
# 则说明cuda不可用，参照网上教程启用cuda
```

## 使用conda

```
conda create venv_name      # 创建虚拟环境，venv_name为环境名
conda activate venv_name    # 激活虚拟环境
pip install .               # 下载依赖（在requirements.txt同级目录下）

# 同上步骤检查cuda
```
