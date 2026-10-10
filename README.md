# SRun 校园网自动认证（srun-campus-auth）

深度适配**深澜 SRun Portal** 校园网的自动认证工具：纯 Python、零第三方依赖、事件驱动、有线优先、掉线自愈。
在 Windows 上配合计划任务可实现**全程无感**：插网线、拔网线、开机、睡眠唤醒都会自动检查并完成认证，再不用打开浏览器点登录。

## 特性

- **协议级认证**：完整实现 SRun `srun_bx1` 加密协议（challenge → HMAC-MD5 → XXTEA 自定义 base64 → chksum），与浏览器 portal.js 逐字节一致，不依赖浏览器
- **有线优先**：多网卡环境下优先使用以太网认证；逐网卡源 IP 绑定 + `client_ip` 自检，避免流量实际走了别的网卡
- **事件驱动**（计划任务）：网络连接 / 网络断开 / 用户登录 / 睡眠唤醒四类事件触发，秒级响应，不做轮询；已在线时 1 秒内静默退出
- **掉线自愈**：被其他设备顶下线后自动重连
- **名额管理**：有线在线时自动注销本机无线会话，腾出在线设备名额（只操作本机网卡）
- **零打扰**：`pythonw` 无窗口运行 + `netsh` 后台调用，没有黑框闪烁；运行历史写入 `auth.log`

## 快速开始（Windows）

1. 安装 [Python 3.8+](https://www.python.org/downloads/)（安装时勾选 **Add Python to PATH**）
2. 复制 `config.example.json` 为 `config.json`，填入四项：

   ```json
   {
     "host": "211.83.x.x",
     "username": "学号@运营商域名后缀",
     "password": "密码",
     "ac_id": "2"
   }
   ```

   - `host`：打开校园网认证页时地址栏的 IP 或域名
   - `username`：融合认证（页面有校园网/移动/电信/联通多个按钮）时，账号要带**域名后缀**（如 `学号@cmcc`、`学号@telecom`），后缀在认证页 HTML 的按钮 `data-value` 里；只有单一"校园网登录"按钮则只填学号
   - `ac_id`：认证页 URL 里的 `ac_id=2` 参数，一般是 `2`
3. 双击 `认证一次.bat` 验证能认证成功
4. 双击 `安装计划任务.bat`（会弹 UAC，点"是"）安装全自动任务

完成后日常使用**零操作**。想确认它在工作，看同目录的 `auth.log`。

## 命令行用法

```bat
python srun_auth.py                  :: 检查状态，掉线则认证（有线优先）
python srun_auth.py --login          :: 强制重新认证
python srun_auth.py --status         :: 只看在线状态
python srun_auth.py --watch          :: 保活模式，每 30 秒检查（适合不想装计划任务时）
python srun_auth.py --interval 60 --watch
python srun_auth.py --adapter "以太网"  :: 指定网卡
python srun_auth.py --host x.x.x.x --username 学号@域名 --password 密码   :: 不用 config.json
```

## 如何查自己学校的参数

1. 浏览器打开认证页，地址栏看到 `http://host/srun_portal_pc?ac_id=2&...` —— `host` 和 `ac_id` 就有了
2. 页面源代码里搜 `btn-login`，每个登录按钮的 `data-value="@xxx"` 就是运营商域名后缀；F12 开发者工具 → Network 里看 `srun_portal` 请求的参数也可以
3. 理论上所有深澜 SRun（`srun_portal_pc` 页面、`/cgi-bin/get_challenge` 接口）的学校都能直接用；如果学校魔改过加密算法，欢迎提 Issue

## 常见问题

**Q: 弹黑色窗口？**
A: 不会。计划任务使用 `pythonw.exe`，且脚本内 netsh 调用带 `CREATE_NO_WINDOW`。如果还能看到闪窗，检查任务操作里是不是用了 `python.exe`。

**Q: 注销后它怎么又自动连上了？**
A: 很多学校开了 MAC 地址无感知认证，注销后数秒内会按网卡 MAC 悄悄恢复会话——这不是 bug，反而和本工具互补。

**Q: 想停用？**
A: 任务计划程序里找到 `SRun校园网自动认证` 右键禁用，或管理员 PowerShell 执行 `Unregister-ScheduledTask -TaskName 'SRun校园网自动认证' -Confirm:$false`。

**Q: 有线老是掉线？**
A: 多半是网卡节电功能。设备管理器 → 有线网卡 → 属性 → 电源管理取消"允许计算机关闭此设备"；高级选项卡关闭节能以太网(EEE)/Green Ethernet/GigaLite/Power Saving Mode。换网线、换墙插口也可能有效。

## 实现要点（给想研究的人）

- 认证流程：`GET /cgi-bin/get_challenge` 取 token → `hmd5 = HMAC-MD5(key=token, msg=password)` → `info = "{SRBX1}" + base64(XXTEA(json, token))`（自定义字母表）→ `chksum = SHA1(token+username+token+hmd5+token+ac_id+token+ip+token+n+token+type+token+info)` → `GET /cgi-bin/srun_portal?action=login&...`
- 事件触发器订阅的是 `Microsoft-Windows-NetworkProfile/Operational` 日志的 EventID 10000（连接）/10001（断开）。**注意：XPath 里不要写 `Provider[@Name='NetworkProfile']`，真实 Provider 名是 `Microsoft-Windows-NetworkProfile`，写错会静默不匹配**（本项目踩过的坑）
- PowerShell 用 `New-CimInstance -ClientOnly` 构造事件触发器时，需要手动补 `MSFT_TaskTrigger` 基类型名，否则 `Register-ScheduledTask` 拒绝

## 免责声明

本项目仅供学习与技术交流，请遵守所在学校的网络使用规定。使用者需自行承担因使用本工具产生的一切责任。不要将包含真实账号密码的 `config.json` 提交到任何公开仓库（`.gitignore` 已默认排除）。

## License

MIT
