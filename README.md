# FkingSportsWorldWeb

运动世界校园刷跑工具：Python/FastAPI 后端 + 浏览器/移动端前端 +
pywebview 打包 Windows EXE。

## 计划文档

### 已完成

- 模块化后端：auth 登录/会话持久化、runarea 区域拉点、stadium 体育场
  拟合、trackgen 轨迹通道生成（海拔/速度/步频/GPS/走位/吸附/统计分文件）、
  wire 协议序列化、submit 提交 + OBS 上传 + 回读验证、receipt 防重复提交
- 设备指纹按账号持久化（device_id/mac/app_install_time）
- 配置卡与拟合/提交卡经 projectConfig.json 同步，保存即重算
- 轨迹真实化：分层海拔模型（H0 样条趋势 + H1 波包 + H2 抖动 + Hg 尖刺，
  极差 ~[14,24]m、上限 alt_cap 可配）、停顿段、步频下限/毛刺、
  GPS 速度 0/瞬移毛刺、配速凹陷
- 拉点用真实时间戳（已删假正午戳）；拟合 start_ms 默认对齐拉点时刻

### 时间线自洽（定时提交方案）— 本地版已落地

**问题**（已解决）：拉点 → 几秒后提交 与真实 App 的
拉点 → 静默整场跑步时长 → 提交 的间隔模式不符。

**已实现的本地版**：

- 进程内定时队列 `queue.jobs`：`run_uuid → {username, body,
  policy_ts, track, five_point, end_ms, fire_at, created_ms,
  attempts}`——**不落盘，关进程即弃**（点了提交就等到点，
  应用关了就当没发生过，config.json 不膨胀）
- `fire_at = 记录结束 + U(45,95)s`，10% 长尾 +5~30min；
  记录已结束则兜底 now+U(45,95)s（不立即提交）
- 后台线程 `_submit_worker` 每 10s 扫描 → 到期 → 账号一致性检查
  （不符挂 waiting_user 留队）→ get_session 自动重登 → 提交 →
  OBS → 回读 → 置 identities[账号].auto=true → 出队；
  失败退避重试 2 次后标 failed
- 排队期间前端全局锁：自动跑/获取打卡点/拟合/调整/保存配置
  全部禁用（job 绑定该次拟合），取消或提交完成后恢复
- `/api/fit` 已删除未来结束钳制：start_ms 锚定拉点时刻，
  结束在未来合法（调度器等到点）
- `/api/submit` 返回 {scheduled, fire_at, eta_s}；
  `/api/submit/status` 返回 pending+最近一次执行结果；
  `/api/submit/cancel` 取消（执行中不可取消）
- 前端：提交按钮变倒计时"自动提交 mm:ss"，旁挂"取消排队"；
  15s 轮询至出队后显示"✓ 已自动提交 rrid=…"；renderFit 时
  恢复已排队 uuid 的倒计时态
- 提交卡新增过时警告：记录结束早于当前 >10min 时黄字提示
  时间戳与服务器日志对不上，可能被追缴

**待做**：

- **通知**：通用 webhook 钩子（`notify.webhook` 可插 Bark/Server酱/
  企微）；本地版附加 win toast 弹窗

**部署形态**：

- 本地（Win）：关闭按钮 → 最小化托盘 → 到期后台提交 → toast 通知；
  可选提交完 N 分钟后登出退出；远期可做每日定时自动刷
- CF 容器：不走常驻等待（烧钱），用 **DO alarm 到点唤醒
  容器**打 `/internal/fire` 入口；job 存该账号 DO storage
  （详见"持久化与部署改进"节）；拒绝纯 JS Worker 重写方案
- 同一套 `_fire_pending(job)` 核心两端共用，仅"唤醒适配器"不同

### 待实现：持久化与部署改进

**1. 持久层抽象（ConfigStore 接口）**

`cfg_load/cfg_save` 的全部调用点不变，底层换成接口 +
两个实现：本地读写 `projectConfig.json`；CF 读写 DO storage
（经 Worker 薄代理端点 `/store/read|write`，Python 容器内进程
摸不到 `ctx.storage`，由 DO 的 JS 代码桥接）。

- **device 指纹必须存服务端按账号归属**——浏览器 localStorage
  只能放 UI 缓存：多浏览器各持一份指纹 = 同账号多台"手机"，
  是硬指纹裂缝；浏览器缓存永不做权威源
- **队列必须服务端**（定时提交前提），identities/anchors/配置
  随迁；容器 FS 是临时的，状态全部放容器外

**2. CF 部署形态：每账号一个 Durable Object**

- 路由：`idFromName(username)` → 该账号 DO；
  DO storage 私有存储该账号全部 JSON（不需要"查表"，
  路由即隔离，天然防串号）
- 定时提交：DO alarm 绑在该账号 DO 上，到点唤醒容器打
  `/internal/fire`——容器可自由 scale-to-zero，唤醒即执行
- 不选 D1：用量极低（单次刷跑 ~10 写、~150KB，月千次级）
  在免费带内；且 D1 无 alarm 机制，跨用户查询能力用不上
- 不选常驻等待：按运行时长烧钱；alarm 唤醒每次刷跑容器
  时长 ~90s，成本主项是容器时长而非存储
- 凭据落云端（token 过期需密码重登）：多人共用部署时
  Web UI 需自带访问控制

**3. 数据生成的三层随机模型**

- **设备层（绑账号，不漂移）**：设备噪声参数由指纹哈希派生——
  `device_seed = sha256(APP_SALT + device_id)` 现算不落盘，
  对 ar1_sigma/accuracy/海拔抖动/毛刺率/漂移率/persona 权重
  做乘性调制（因子钳 [0.5,2.0]）。同账号=同手机=同噪声体质；
  不用账号密码做种子（改密码=换手机 + 口令混入对外可见派生值）
- **跑者层（绑账号权重）**：persona 偏好持久化按账号偏向
  （如某账号 70% 稳/25% 常规/5% 崩），体现"同一个人惯常跑法"
- **发挥层（每次随机）**：凹陷位置、波包形状、停顿时点、
  配速画像（平稳巡航/常规起伏/突变型/跑崩）按账号权重抽样——
  行为通道逐记录合法波动，解释真图中平滑与突变共存
- 同账号同 seed 同配置 → 同轨迹（哈希派生附带可复现性）

### 遗留观察项

**探针实锤的字段差异**（与 `backend/src/png` 下真机截图对比，
由一次性探针脚本量化后记录于此，脚本已清理）：

- `validDis`/`validTime` 恒等于 `totalDis`/`totalTime`
  （探针：0 差异点；真记录漂移段不计有效里程，valid < total 常见）
- `radius`/`accuracy` 永远 ≤5.0m（探针：0 个超 5m 点；
  真机偶发 5-15m 精度崩坏，树荫/高楼旁）
- `totalDis` 纯速度积分无跳变（真记录 GPS 瞬移造成 +20-40m
  里程跳变——配速图 0'00" 尖刺的来源；应使漂移跳步同步
  累加里程并联动 avgSpeed 毛刺）
- 圈间轨迹偏移 ~2m（真记录 2-5m 整体慢漂，每圈不完全重叠）

**已确认未改的**：

- GPS 位置噪声量级偏小：AR(0.72) σ0.35 稳态 std≈0.5m
  vs 真机 ±2-5m——轨迹贴拟合环太死（改 ar1_sigma +
  分钟级圈间慢漂项）
- `bdD` 朝向：漂移/停顿段仍沿行进方向 ±35°，
  真机漂移时朝向完全乱飘
- 配速毛刺未打通：`speed` 字段已有 0-55km/h 毛刺，
  但 avgSpeed/分段缺瞬移型尖刺（依赖里程跳变联动）

**基本对齐的**：

- 圈配速方差 39-99s（停顿/凹陷生效），真图 ~108s ✓
- bdS 步幅 [0~2.75m] ≈ 真图范围 ✓
- 海拔/步频/配速凹陷/GPS 速度毛刺 ✓

**无法验证的盲项**（缺真实原始 JSON 比对）：

- `count` 字段（卫星数？现 randint 20-88 拍脑袋）
- `bdG`、`locType` 细分、`queueNum`/`dtr` 常量
- `build_record_body` 顶层字段未与真实抓包比对——
  封号分析需要时第一个对这里

- 每公里配速 "3'19'00"" 渲染待真实记录核对
- `_fence_test.py` 保持冻结不动
