# 常用动作的快捷入口。没有 make 也能直接用 python ops\tasks.py <cmd>
#
#   make status     端口 / QQ 连接 / 台账 / 日志错误数
#   make test       跑 8 套测试
#   make check      数据与知识库一致性
#   make backup     在线备份
#   make backup-stop  先停服务再备份
#   make drill      归档级演练
#   make drill-full 独立目录恢复演练
#   make patches    补丁包校验
#   make restart    重启 AstrBot
#   make butler     重启管家

PY := instances/11111111-1111-4111-8111-111111111111/venv/Scripts/python.exe
TASKS := $(PY) ops/tasks.py

.PHONY: status test check backup backup-stop drill drill-full patches restart butler

status:
	$(TASKS) status

test:
	$(TASKS) test

check:
	$(TASKS) check

backup:
	$(TASKS) backup

backup-stop:
	$(TASKS) backup --stop-service

drill:
	$(TASKS) drill

drill-full:
	$(TASKS) drill-full

patches:
	$(TASKS) patches

restart:
	$(TASKS) restart-astrbot

butler:
	$(TASKS) restart-butler
