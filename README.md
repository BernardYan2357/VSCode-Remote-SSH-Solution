# VSCode Remote SSH Solution

面对 VSCode Remote SSH 连接远程服务器时，长期卡在 Downloading VS Code Server 的问题，提供了一个可在 windows 或 linux 上运行的脚本，帮助用户在远程服务器上手动安装 VSCode Server。

> 脚本思路来自[我只教一次！vscode remote-ssh 连接失败的基本原理和优雅的解决方案 - 知乎](https://zhuanlan.zhihu.com/p/671718415)。

脚本流程图：

```mermaid
flowchart TD
    A[启动脚本] --> B{判断运行系统}

    B -->|Windows| W1{是否传入 commitId?}
    W1 -->|否| W2[执行 code --version 获取 commitId]
    W1 -->|是| W3[使用传入的 commitId]
    W2 --> W4{选择下载方式}
    W3 --> W4
    W4 -->|浏览器下载| W5[打开浏览器并监听 Downloads 中的新下载]
    W4 -->|监听已有或新下载| W6[扫描 Downloads 中已有及新下载的压缩包]
    W4 -->|命令行下载| W7[下载 Linux Server 压缩包]
    W5 --> M1[等待非空压缩包稳定且无下载临时文件]
    W6 --> M1
    M1 --> M2[读取包内 product.json 的 commit ID]
    M2 -->|读取成功| M3{是否匹配目标 commit ID?}
    M2 -->|无法读取| M4[提示并跳过无效压缩包]
    M4 --> M1
    M3 -->|否| M5[输出 WARNING：文件名、实际及目标 commit ID]
    M5 --> M1
    M3 -->|是，复制到临时目录| W8[获得本地临时压缩包]
    W7 --> W8
    W8 --> C1{上传前校验包内 commit ID 是否匹配?}
    C1 -->|否或无法读取| C2[报错并停止传输]
    C1 -->|是| W9[scp 上传压缩包到 Linux]
    W9 --> W10[ssh 远程创建目录并解压]
    W10 --> W11[删除 Linux 临时压缩包]
    W11 --> Z[完成]

    B -->|Linux| L1{是否传入 commitId?}
    L1 -->|否| L2[报错并退出]
    L1 -->|是| L3[扫描 /home 下各用户的 vscode-server]
    L3 --> L4{找到相同 commitId?}
    L4 -->|是| L5[移动 Server 到当前用户目录]
    L4 -->|否| L6[从 update.code.visualstudio.com 下载]
    L6 --> L7[校验压缩包并解压]
    L7 --> Z
    L5 --> Z
```

## Usage

```bash
# windows
python VSCodeReomteSSH.py --sshHost <your_ssh_host> --commitId <commit_id> # commitId 可选，若不传入则自动获取
# linux
python VSCodeReomteSSH.py --commitId <commit_id>
# 指定远端架构为 ARM64
python VSCodeReomteSSH.py --sshHost rdk_s100 --arch arm64 # --arch 可选值为 x64、arm64，不传时由脚本自动识别；检测失败会报错
```

## Environment Requirements

- VSCode；
- Python 3.10 或更高版本。
