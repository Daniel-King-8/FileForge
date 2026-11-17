# FileForge（文件工坊）

面向普通用户的**容器化在线格式转换平台**。注册登录后上传文件，选择目标格式，服务端在**一次性隔离容器**中完成转换，再由 Nginx 直接下发结果。

全栈 `docker compose up -d` 部署，包含 `nginx / app / worker / cleanup` 四个常驻服务，外加两个按需构建的转换工具镜像。

---

## 一、功能一览

| 模块 | 实现 |
| --- | --- |
| 用户系统 | 注册 / 登录 / 登出，bcrypt（cost 12）密码哈希，登录失败 15 分钟内 5 次锁定，封禁用户拦截 |
| 配额 | 单文件 200MB、单用户 2GB，上传累计、删除/过期自动释放 |
| 上传 | 拖拽 + 多选，XHR 进度条，前端大小/类型预检，后端读 4KB 文件头做 libmagic + 魔数校验 |
| 图片 | jpg/png/webp/gif/tiff/bmp 互转，压缩质量、等比缩放；worker 内置 Pillow |
| 文档 | docx/doc/odt → pdf/html/txt，xlsx/xls/ods → pdf/csv/html，pptx/ppt → pdf，md → html/pdf/txt；LibreOffice 容器 |
| 音视频 | mp4/mkv/avi/webm/mov 互转，提取音频（mp3/m4a/wav/flac/aac/ogg），视频转 GIF，片段截取；ffmpeg 容器 + `-progress` 解析 |
| 压缩包 | zip/7z/tar.gz 互转、在线查看清单；7z 容器；解压前做 zip 炸弹与路径穿越检查 |
| 队列 | SQLite `tasks` 表，`UPDATE ... RETURNING` 原子领取，全局并发上限，多 worker 可横向扩展 |
| 进度 | SSE 实时推送，ffmpeg 按时间轴计算百分比，页面可查看每任务日志 |
| 下载 | 鉴权后返回 `X-Accel-Redirect`，Nginx 直接从 results 卷发文件 |
| 分享 | 随机 token 公开链接，可设有效期与下载次数，页面不暴露上传者 |
| 清理 | cleanup 容器每小时清理过期任务/文件/分享/审计，回收失联任务与孤儿文件 |
| 管理后台 | 磁盘水位、队列积压、worker 心跳、docker stats、用户封禁/重置密码、告警确认、审计日志 |
| 审计/告警 | 注册登录、上传、转换、下载、分享、管理操作全部落 `audit_logs`；磁盘 85% 告警、95% 拒绝新任务 |

---

## 二、架构

```
                         浏览器
                           │  :80
                           ▼
                 ┌───────────────────┐
                 │      nginx        │  静态文件 / 反代 / X-Accel 下载
                 └─────────┬─────────┘
                           │ fileforge_net (bridge)
          ┌────────────────┼─────────────────┐
          ▼                ▼                 ▼
     ┌─────────┐     ┌──────────┐     ┌───────────┐
     │   app   │     │  worker  │     │  cleanup  │
     │ Gunicorn│     │ 队列消费 │     │  定时清理 │
     └────┬────┘     └────┬─────┘     └─────┬─────┘
          │               │                 │
          └───────────────┴─────────────────┘
                          │
              SQLite (data 卷, WAL) + uploads/tmp/results/logs 命名卷
                          │
                 worker 通过 /var/run/docker.sock
                          ▼
        ┌──────────────────────────────────────────┐
        │ 一次性工具容器 docker run --rm            │
        │ --network none --memory 1g --cpus 1      │
        │ --pids-limit 64 --user 1000:1000         │
        ├──────────────────────────────────────────┤
        │ fileforge/media-tools:1.0.0  (ffmpeg/7z) │
        │ fileforge/office-tools:1.0.0 (LibreOffice)│
        └──────────────────────────────────────────┘
```

### 服务清单

| 服务 | 镜像 | 职责 | 端口 |
| --- | --- | --- | --- |
| `volume-init` | 自建 app 镜像 | 一次性把命名卷 chown 给 uid 1000，正常退出 | 无 |
| `nginx` | `nginx:1.27-alpine` | 反代、静态文件、X-Accel 下载加速 | 80 |
| `app` | `fileforge/app:1.0.0` | Web 接口、用户系统、任务管理（Gunicorn） | 内部 8000 |
| `worker` | 同 app 镜像 | 消费队列、启动工具容器 | 无 |
| `cleanup` | 同 app 镜像 | 定时清理过期数据 | 无 |
| `tools-media` | `fileforge/media-tools:1.0.0` | 仅构建，不常驻 | 无 |
| `tools-office` | `fileforge/office-tools:1.0.0` | 仅构建，不常驻 | 无 |

> `volume-init` 是一次性容器，`docker compose ps -a` 中显示为 `Exited (0)`，属正常现象。

### 命名卷

| 卷 | 名称 | 用途 | 挂载 |
| --- | --- | --- | --- |
| `data` | `fileforge_data` | SQLite 数据库 | app/worker/cleanup 读写 |
| `uploads` | `fileforge_uploads` | 原始上传文件 | app/worker/cleanup 读写 |
| `tmp` | `fileforge_tmp` | 每任务转换工作目录 | app/worker/cleanup 读写，转换容器按子路径挂载 |
| `results` | `fileforge_results` | 转换结果 | app/worker/cleanup 读写；nginx **只读** |
| `logs` | `fileforge_logs` | 每任务转换日志 | app/worker/cleanup 读写 |

---

## 三、目录结构

```
FileForge/
├── app.py                    # Flask 应用、页面路由
├── config.py                 # 全部配置（环境变量驱动）
├── db.py                     # SQLite 连接 / 初始化
├── models.py                 # 用户、任务、分享、审计、告警等数据访问
├── auth.py                   # 注册 / 登录 / 改密 / 权限装饰器
├── security.py               # bcrypt 封装
├── sniff.py                  # libmagic + 魔数文件类型识别
├── upload.py                 # 上传接口
├── tasks.py                  # 任务 API / SSE / 下载 / 分享 / 批量
├── admin.py                  # 管理员 API
├── alerts.py                 # 磁盘水位与告警
├── worker.py                 # 队列消费
├── cleanup.py                # 定时清理
├── formats.py                # 格式白名单（前后端共用）
├── utils.py                  # 时间/文件/格式化工具
├── converters/
│   ├── base.py               # docker run 封装、超时 kill、进度、日志
│   ├── image.py              # Pillow
│   ├── document.py           # LibreOffice 容器 + Markdown
│   ├── media.py              # ffmpeg 容器 + 进度解析
│   └── archive.py            # 7z 容器 + 炸弹防护
├── templates/                # 9 个页面模板
├── static/css/app.css
├── static/js/{app,auth,upload,tasks,task,profile,admin}.js
├── deploy/
│   ├── nginx.conf
│   ├── init.sql
│   └── tools/{media,office}.Dockerfile
├── scripts/{deploy.sh,check-env.sh}
├── tests/test_smoke.py       # 本地冒烟测试（不需要 Docker）
├── Dockerfile
├── docker-compose.yml
├── gunicorn.conf.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## 四、快速开始（Linux 服务器）

### 4.1 前置条件

- Linux（amd64 / arm64），Docker Engine 24+，Docker Compose v2
- 建议 Docker Engine **25+**（支持 `volume-subpath`；低版本会自动降级）
- 2 vCPU / 2GB 内存以上，磁盘预留 10GB（LibreOffice 镜像约 700MB）
- 首次构建需要访问 Docker Hub（或你配置的镜像加速器），无需额外下载第三方二进制

### 4.2 一键部署

```bash
git clone <你的仓库地址> fileforge
cd fileforge
bash scripts/deploy.sh
```

脚本会：生成 `.env`（随机 SECRET_KEY、管理员密码、自动探测 docker.sock 的 GID）→ 构建应用镜像 → 构建两个工具镜像 → 启动全栈 → 健康检查。

脚本结束会打印访问地址和管理员账号。

### 4.3 手动部署

```bash
cp .env.example .env
vim .env          # 至少填 SECRET_KEY、ADMIN_PASSWORD、DOCKER_GID

docker compose build                    # 应用镜像
docker compose --profile tools build    # 工具镜像（ffmpeg/7z、LibreOffice）
docker compose up -d

docker compose ps -a
curl http://127.0.0.1/api/health
```

**只执行 `docker compose up -d` 而不构建工具镜像是常见的坑**：图片转换（Pillow 在 worker 内）仍可用，但文档/音视频/压缩包会失败并提示“工具镜像不存在”。

### 4.4 管理员账号

- 账号：`.env` 中的 `ADMIN_USERNAME`（默认 `admin`）
- 密码：`.env` 中的 `ADMIN_PASSWORD`
- 如果未设置密码，首次启动会生成随机密码并打印在 `docker compose logs app`，搜索“随机密码”

访问 `http://<服务器IP>/`，注册普通账号或使用管理员登录后进入 `/admin`。

---

## 五、主要配置项

所有配置都在 `docker-compose.yml` 的 `x-app-env` 中，可通过 `.env` 覆盖。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | dev 占位 | Flask session 签名密钥，部署脚本自动生成 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | admin / 空 | 首次初始化管理员；密码为空则随机生成 |
| `DOCKER_GID` | 0 | 宿主机 docker.sock 的组 ID，worker 用它获得访问权限 |
| `HTTP_PORT` | 80 | Nginx 对外端口 |
| `MAX_UPLOAD_MB` | 200 | 单文件上限 |
| `USER_QUOTA_MB` | 2048 | 单用户配额 |
| `RESULT_TTL_HOURS` | 24 | 结果保留时间 |
| `MAX_CONCURRENT` | 2 | 全局同时转换任务数 |
| `TIMEOUT_IMAGE/DOCUMENT/MEDIA/ARCHIVE` | 60/180/600/120 | 各类转换超时（秒） |
| `TOOL_MEMORY` / `TOOL_CPUS` / `TOOL_PIDS_LIMIT` | 1g / 1 / 64 | 转换容器限制 |
| `TOOL_MOUNT` | subpath | `subpath` 只挂载任务子目录；`volume` 为旧 Docker 兼容模式 |
| `DISK_WARN_PCT` / `DISK_STOP_PCT` | 85 / 95 | 磁盘告警 / 拒绝新任务阈值 |
| `CLEANUP_INTERVAL` | 3600 | cleanup 执行间隔（秒） |
| `SECURE_COOKIES` | 0 | 走 HTTPS 时设为 1 |
| `LIBMAGIC` | auto | auto=Linux 启用 libmagic、其他平台只用内置魔数；可强制 on/off |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` | 2 / 8 | app 并发 |

---

## 六、关键设计（答辩重点）

### 6.1 转换容器隔离

`converters/base.py` 中所有转换工具都以一次性容器执行：

```bash
docker run --rm \
  --network none \
  --memory 1g --memory-swap 1g \
  --cpus 1 \
  --pids-limit 64 \
  --user 1000:1000 \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,size=512m,mode=1777 \
  --mount type=volume,source=fileforge_tmp,target=/work,volume-subpath=<task_id> \
  -w /work \
  fileforge/media-tools:1.0.0 ffmpeg ...
```

- `--network none`：转换进程完全断网
- `--memory-swap` 与 `--memory` 相等：禁用 swap
- `--pids-limit 64`：防 fork 炸弹
- `--rm`：成功/失败/被杀后容器自动删除
- 超时或用户取消：watchdog 线程执行 `docker kill <name>`
- 命令以 list 传参，不经过 shell，避免命令注入
- 音视频进度：ffmpeg 用 `-progress pipe:1` 输出 `out_time_us`，再结合 ffprobe 时长计算百分比，比解析 stderr 的 `time=` 更稳定

### 6.2 为什么不用 `-v /srv/tmp/<id>:/work`

要求文档里的 `-v` 写法在「worker 本身也在容器里」时会挂到宿主机上不存在的路径，结果是一个空目录。本项目的处理：

1. `tmp` 是命名卷 `fileforge_tmp`；
2. 转换容器用 `--mount type=volume,...` 挂载整个卷；
3. 优先使用 `volume-subpath=<task_id>`，让每个转换容器只能看到自己任务的目录；Docker < 25 时自动降级为整卷挂载并写日志。

### 6.3 Docker socket 的安全处理

worker 必须挂载 `/var/run/docker.sock`，这是方案本身的已知风险。缓解措施：

- socket 只读挂载（`:ro`）
- worker 容器 `cap_drop: ALL`、`no-new-privileges:true`
- worker 以非 root（uid 1000）运行，只加入 socket 所在组
- app **不挂载** socket：管理后台的 `docker stats` 由 worker 定时采集写入 `container_stats` 表，管理员页面读表

生产环境建议改用 Docker API over TLS、rootless Docker 或 socket proxy（如 tecnativa/docker-socket-proxy）。

### 6.4 队列原子领取

```sql
UPDATE tasks
   SET status='running', worker_id=?, started_at=?, progress=0
 WHERE status='pending'
   AND target_format IS NOT NULL AND target_format <> ''
   AND (SELECT COUNT(*) FROM tasks WHERE status='running') < ?
   AND id = (SELECT id FROM tasks
              WHERE status='pending' AND target_format IS NOT NULL AND target_format <> ''
              ORDER BY created_at ASC, rowid ASC LIMIT 1)
RETURNING *;
```

配合 `BEGIN IMMEDIATE`、SQLite WAL 与 `busy_timeout=5000`，多 worker 不会重复领取。
`target_format IS NOT NULL` 保证“刚上传、还没选目标格式”的任务不会被提前消费。

### 6.5 上传三重校验

1. 扩展名必须在对应分类白名单内；
2. 读前 4KB，用 python-magic（容器内 libmagic）判断 MIME；
3. 同时用内置魔数解析（PNG/JPEG/ZIP/OLE/MP4/7z…），ZIP 再下钻识别 docx/xlsx/pptx/odt；
4. 两者与扩展名不一致直接 415。

文件名只保留 basename，落盘用 UUID 重命名，杜绝路径穿越与中文名兼容问题。

> Windows 开发机上 python-magic 常因缺少 DLL 崩溃，`sniff.py` 在非 Linux 平台默认关闭 libmagic，仅使用内置魔数；容器内（Linux）默认启用。

### 6.6 下载加速

app 鉴权通过后返回空响应 + `X-Accel-Redirect: /_files/<uuid>`，Nginx 的 `internal` location 从 `results` 卷直接读取文件：

```nginx
location /_files/ {
    internal;
    alias /srv/results/;
}
```

大文件下载不占用 Python 进程，`results` 卷以只读方式挂给 nginx。

> 要求文档提到“302 到 /_files”，实际用 `X-Accel-Redirect` 响应头即可触发 Nginx 内部跳转，无需 302；`/_files/` 是 `internal` location，外部无法直接访问。

### 6.7 资源限制

- app / worker / cleanup 在 compose 中用 `deploy.resources.limits` 限制 CPU 与内存；
- 转换容器在 `docker run` 参数里单独限制，比 compose 限制更严格；
- worker 内存 512M，因此 Pillow 把解码上限设为 4000 万像素，避免超大图把 worker 撑爆；
- 图片转换在 worker 内执行，除 60 秒超时外还用 `SIGALRM` 做兜底（Linux），其余三类由容器 watchdog 控制。

### 6.8 管理后台的系统状态

- 磁盘：`shutil.disk_usage` 读各卷所在文件系统，`os.walk` 统计卷内容大小
- 队列：`tasks` 按状态聚合
- worker：每 10 秒心跳
- 容器资源：worker 每 20 秒执行 `docker stats --no-stream` 写入 `container_stats`

---

## 七、演示流程（对应验收清单）

```bash
# 1. 启动与状态
docker compose up -d
docker compose ps -a          # 4 个 running + volume-init Exited(0)
curl http://127.0.0.1/api/health

# 2. 注册登录后：图片 jpg→webp
#    浏览器操作，任务详情页可看到 SSE 进度与日志

# 3. 文档 docx→pdf（观察工具容器启动）
docker compose up -d
watch -n1 'docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"'

# 4. 音视频 mp4→gif（SSE 进度实时）
#    任务详情页会显示 ffmpeg 时间轴进度

# 5. worker 日志
docker compose logs -f worker

# 6. 横向扩展
docker compose up -d --scale worker=2
docker compose ps

# 7. 伪造后缀
#    把 .txt 改名成 .png 上传，页面提示“文件真实类型为 text，与扩展名不符”

# 8. 转换容器断网验证
docker run --rm --network none --entrypoint bash fileforge/media-tools:1.0.0 \
  -c 'timeout 3 bash -c "echo > /dev/tcp/1.1.1.1/80" || echo "network blocked"'

# 9. X-Accel 下载（查看响应头）
curl -sI http://127.0.0.1/download/<task_id>          # 需要带登录 Cookie
# 响应中可见 X-Accel-Redirect: /_files/<uuid>
# 浏览器下载响应头中有 X-Accel-Served: nginx

# 10. 分享过期演示
docker compose exec app python -c "import sqlite3, config; \
  c=sqlite3.connect(config.cfg.db_path); \
  c.execute(\"UPDATE shares SET expire_at='2000-01-01T00:00:00Z'\"); c.commit()"
# 刷新分享页 → 404 提示已过期

# 11. cleanup
docker compose exec -e CLEANUP_ONCE=1 cleanup python cleanup.py

# 12. 资源限制
docker stats --no-stream
# 管理后台 /admin 概览页也能看到 app/worker 的 CPU/内存

# 13. 审计
# 管理后台 → 审计 标签页，或：
docker compose exec app python -c "import sqlite3, config; \
  c=sqlite3.connect(config.cfg.db_path); \
  [print(r) for r in c.execute('SELECT action, target, created_at FROM audit_logs ORDER BY id DESC LIMIT 20')]"
```

### 超时 / OOM 演示

```bash
# 把音视频超时临时改成 10 秒
sed -i 's/^TIMEOUT_MEDIA=.*/TIMEOUT_MEDIA=10/' .env   # 若 .env 没有该行则追加
docker compose up -d worker
# 上传一个较大的 mp4 转 webm，观察：
#   logs/tasks/<id>.log 出现 [timeout] 与 docker kill
#   任务状态变为 failed，错误为“转换超时（10s），容器已被 kill”
```

---

## 八、验收清单对照

| 验收项 | 验证方式 |
| --- | --- |
| `docker compose up -d` 一键启动 | `docker compose ps` 四服务 running（另有 Exited 的 volume-init） |
| 注册登录、防暴力破解 | 连续 5 次错误密码后提示 15 分钟后再试 |
| 图片 jpg→webp | 转换页上传 → 任务页/详情页见进度 → 下载 |
| docx→pdf | 观察 `docker ps` 出现 `ff-xxxx` 一次性容器 |
| mp4→gif 与 SSE | 详情页进度条随时间上涨 |
| 伪造后缀被 magic 拒绝 | 上传改名文本文件 → 415 提示真实类型 |
| 转换超时被 kill | 调小 `TIMEOUT_*` 后观察日志 `docker kill` |
| `--network none` | 见演示步骤 8，输出 network blocked |
| X-Accel-Redirect | 下载响应头 / Nginx 配置 / 任务日志 |
| 分享过期 404 | 修改 `shares.expire_at` 后刷新分享页 |
| cleanup 删除过期文件 | `CLEANUP_ONCE=1` 后检查 uploads/results |
| `--scale worker=2` | `docker compose ps` 出现两个 worker，队列消费加快 |
| `docker stats` 资源限制 | 宿主机 `docker stats` 与 `/admin` 容器资源表 |
| 审计记录完整 | `/admin` 审计标签页 |

---

## 九、常见问题

**Q1：worker 日志报 `permission denied while trying to connect to the Docker daemon socket`**

宿主机 docker.sock 属于 `docker` 组（常见 gid 999）。执行：

```bash
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=$DOCKER_GID/" .env
docker compose up -d worker
```

**Q2：转换任务失败，提示“工具镜像 xxx 不存在”**

```bash
docker compose --profile tools build
docker images | grep fileforge
```

**Q3：旧版 Docker 报 `invalid mount ... volume-subpath`**

在 `.env` 中设置 `TOOL_MOUNT=volume`，然后 `docker compose up -d worker`。
程序也会在检测到该错误时自动降级并写日志。

**Q4：80 端口被占用**

`.env` 设置 `HTTP_PORT=8080`，`docker compose up -d nginx`，访问 `http://IP:8080`。

**Q5：LibreOffice 第一次转换很慢**

首次启动需要初始化用户配置目录并加载字体，第二次开始正常。文档超时默认 180 秒，足够。

**Q6：中文 PDF 出现方框**

office 工具镜像已安装 `fonts-noto-cjk`。如果自己换成其他镜像，需要自行安装中文字体。

**Q7：上传大文件 413**

- Nginx：`client_max_body_size 210m`
- Flask：`MAX_CONTENT_LENGTH = MAX_UPLOAD_MB + 2MB`
- 前端：按 `MAX_UPLOAD_MB` 预检

三处要同时大于你要测试的文件。

**Q8：磁盘 95% 后无法上传**

这是设计行为（`DISK_STOP_PCT`）。删除旧任务或在 `/admin` 点“立即清理”，也可等 cleanup 自动执行。

**Q9：Windows 上能开发吗？**

可以跑 Flask + Pillow 图片转换与全部页面（`python app.py`），但文档/音视频/压缩包依赖 Docker 工具容器，且本机未安装 Docker 时 worker 会报错。生产运行请用 Linux。

---

## 十、安全说明与生产建议

1. **Docker socket 是最大风险**：挂载 socket 等价于宿主机 root 权限。已做只读挂载、cap_drop、非 root、no-new-privileges，但生产应换 Docker API + TLS / rootless / socket proxy。
2. 转换容器 `--network none`、资源受限、`--rm`，但**不处理恶意文件漏洞**（如 ffmpeg/LibreOffice 的 CVE）。面向公网时应加病毒扫描与沙箱（gVisor / Kata）。
3. 上传做真实类型校验、UUID 重命名、basename 化；压缩包做炸弹与穿越检查，但 7z 解压仍可能耗尽磁盘，生产建议加 quota/独立磁盘。
4. 密码 bcrypt(cost 12)，session HttpOnly + SameSite=Lax；登录失败按用户名+IP 限速。API 仅接受 JSON，跨站表单难以利用，但生产建议再加 CSRF token。
5. 下载/分享链接均为随机 128bit token，分享按次数与时间限制，页面不展示用户信息。
6. 定期备份 `fileforge_data` / `fileforge_uploads` / `fileforge_results` 卷。

---

## 十一、本地开发与测试

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python tests/test_smoke.py
```

冒烟测试不需要 Docker，覆盖：页面渲染、注册登录、伪造后缀拒绝、图片转换、队列 worker、SSE、下载头、分享、管理员接口、cleanup、删除与配额释放。

本地直接启动 Web：

```bash
python app.py        # http://127.0.0.1:8000
```

本地运行时数据默认落在项目下 `runtime/`（已被 `.gitignore` / `.dockerignore` 排除）。

---

## 十二、答辩要点

1. **一键部署 vs 逐条命令**：`docker compose up -d` 拉起全栈，工具版本由镜像 tag 固定，不污染宿主机。
2. **容器级资源隔离**：`--memory 1g --cpus 1 --pids-limit 64 --network none`，比 `setrlimit` 更声明式、更彻底；超时用 `docker kill`。
3. **工具镜像管理**：ffmpeg/7z 与 LibreOffice 分别构建，固定 `1.0.0`，升级只改 tag 并重跑转换容器。
4. **横向扩展**：worker 无状态，`--scale worker=2` 后依靠 SQLite 原子领取与全局并发上限避免重复消费。
5. **卷持久化**：五个命名卷；`docker compose down` 后重新 `up`，任务历史仍在。
6. **安全设计主动坦白**：docker.sock 的风险、缓解措施与生产替代方案；上传魔数校验；zip 炸弹防护；`--network none` 演示。
7. **路径细节**：讲清“容器内 worker 看到的 `/srv/tmp` 不是宿主机路径”，因此用命名卷 + `volume-subpath`，以及旧版 Docker 的降级策略。
8. **可观测性**：审计表、每任务日志、worker 心跳、容器资源采集、磁盘水位告警、cleanup 回收失联任务。

---

## 十三、已知限制

- SQLite 单机，无法多节点共享；适合课程/单服务器场景
- 图片转换在 worker 内完成（Pillow），不走工具容器；其余三类走容器
- xlsx → csv 只导出第一个工作表（LibreOffice 行为）
- GIF 最多保留 300 帧
- 文档 → HTML 可能丢失复杂排版中的图片资源
- 未做病毒扫描、OCR、PDF 拆分等扩展功能
- 管理后台的容器资源数据依赖 worker 心跳采集，worker 全部停止时最多展示 10 分钟内的旧数据

