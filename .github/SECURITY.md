# 安全策略（SECURITY）

## 支持的版本

本仓库为单主干开发，安全修复只针对 `main` 分支最新提交，请先拉取最新代码再确认问题是否仍存在。

## 供应链说明（abogus.py）

本仓库主体 MIT，但**不包含、也不分发** GPLv3 组件 `scripts/abogus.py`
（抖音 a_bogus 签名算法）。该文件由 `scripts/ensure_abogus.py` 在使用者机器上
按需下载，并采取以下供应链防护措施：

1. **commit 固定**：下载 URL 固定到上游仓库（JefferyHcool/BiliNote）的
   具体 commit（`PINNED_COMMIT` 常量），不跟随分支漂移；
2. **SHA-256 校验**：下载内容必须与 `EXPECTED_SHA256` 常量（该 commit 文件的
   实测哈希真值）一致，不符即拒绝落盘并打印实测值；
3. **镜像不豁免**：ghproxy 镜像仅作为传输通道，镜像下载的内容同样必须通过
   SHA-256 与关键字校验；
4. **本地复检**：已存在的本地文件每次使用前复检哈希，被篡改时自动重新下载；
5. **显式覆盖**：环境变量 `BILINOTE_ABOGUS_SHA` 可指向其他 revision，但此时
   无法预知哈希，脚本只做标记 + 编译校验并打印醒目警告。

报告涉及上述机制被绕过、哈希常量被投毒、下载源劫持等问题时，请在标题中标注
`[supply-chain]` 并按高危处理。

## 漏洞报告方式

**请勿用公开 issue 报告安全漏洞。**

- 首选：GitHub 私密漏洞报告（仓库 Security 标签页 → Report a vulnerability）；
- 备选：通过仓库作者的 GitHub Profile 联系方式私下联系。

报告时请包含：影响范围（哪个脚本/流程）、复现步骤或 PoC、影响评估。
预计 7 天内给出初步回复，修复后会在 release note 中致谢报告者（除非要求匿名）。

## 已知边界（非漏洞）

- 抖音/ B站 接口风控升级导致功能临时失效属可用性问题，请走普通 issue；
- `abogus.py` 的许可头自相矛盾（GPLv3 / Apache-2.0 并存），本项目已按更严格的
  GPLv3 从严处理并在 README 说明，不作为安全问题重复报告。
