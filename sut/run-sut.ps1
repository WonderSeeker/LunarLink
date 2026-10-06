<#
  ============================================================
   sut\run-sut.ps1 —— SUT（被测系统）本地启动器
  ============================================================
   为什么需要它（而不是直接在 svc.ps1 里调 python）：

     1. 测试后门的开关放在启动器里。
        sut/config.py 里的默认值是【关闭】（安全默认，避免后门出现在
        非测试环境）；本地与 CI 是测试环境，所以由启动器显式打开。

     2. 让进程命令行里带上 sut 目录，svc.ps1 的进程匹配更准。

   由 D:\Projects\svc.ps1 以短名 `sut` 调用，也可直接运行本脚本。
   本文件必须保存为 UTF-8 with BOM（PowerShell 5.1 否则按 GBK 读会乱码）。
#>
$ErrorActionPreference = 'Continue'

$env:SUT_HOST = '127.0.0.1'
$env:SUT_PORT = '8021'
# 本地/测试环境打开测试后门（/api/_test/*），让 token 过期、订单支付超时、
# 账号锁定等场景可被确定性构造，测试里不需要 sleep。
$env:SUT_ENABLE_TEST_HOOKS = '1'

$python = 'D:\Projects\LunarLink\backend\.venv\Scripts\python.exe'
$entry = 'D:\Projects\LunarLink\sut\run.py'

Set-Location -LiteralPath 'D:\Projects\LunarLink\sut'
& $python $entry
