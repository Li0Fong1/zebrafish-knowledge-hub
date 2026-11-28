import warnings
warnings.filterwarnings("ignore")

from typing import Optional

import os

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from common_utils import logger
from config import DB_CONFIG

try:  # 只读账号是可选增强：老版本 config 没有该键时不应 import 失败
    from config import DB_READONLY_CONFIG
except ImportError:  # pragma: no cover
    DB_READONLY_CONFIG = None


class MySQLClient:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        # 只读连接（可选）：MYSQL_RO_USER 配置后，所有 SELECT 走这里。
        self.ro_engine = None
        self.RoSessionLocal = None
        self._connect()

    @staticmethod
    def _url(cfg: dict) -> str:
        # 口令里可能出现 @ / : / % 等字符，必须转义，否则 URL 会被解析错
        from urllib.parse import quote_plus

        return (
            f"mysql+pymysql://{quote_plus(cfg['user'])}:{quote_plus(cfg['password'])}"
            f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
            f"?charset={cfg['charset']}&connect_timeout=5"
        )

    def _make_engine(self, cfg: dict):
        # 2026-11-13：给连接数封顶 + 用前探活。以前默认 max_overflow=10，
        # 主/只读两个 engine 最多 30 条常驻；线上曾出现单进程占满 151 条
        # MySQL 连接、外部新连接全部 1040 的情况。单例 + 上限双保险。
        engine = create_engine(self._url(cfg), echo=False, pool_size=5,
                               max_overflow=5, pool_timeout=10,
                               pool_pre_ping=True, pool_recycle=1800)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine

    def _connect(self):
        """建立MySQL连接（主账号 + 可选只读账号）。"""
        try:
            self.engine = self._make_engine(DB_CONFIG)
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            logger.info("[成功] MySQL连接成功")
        except Exception as e:
            logger.error(f"[错误] MySQL连接失败：{e}")
            raise

        # 只读账号：连不上不影响主流程，读查询自动回退主账号
        self.ro_engine = None
        self.RoSessionLocal = None
        ro_cfg = DB_READONLY_CONFIG
        if ro_cfg and ro_cfg.get("user") and ro_cfg.get("user") != DB_CONFIG.get("user"):
            try:
                self.ro_engine = self._make_engine(ro_cfg)
                self.RoSessionLocal = sessionmaker(
                    autocommit=False, autoflush=False, bind=self.ro_engine
                )
                logger.info(
                    "[成功] MySQL只读连接成功（%s@%s:%s）",
                    ro_cfg.get("user"), ro_cfg.get("host"), ro_cfg.get("port"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[MySQL] 只读账号连接失败，SELECT 回退主账号：%s", e)
                try:
                    if self.ro_engine is not None:
                        self.ro_engine.dispose()
                except Exception:
                    pass
                self.ro_engine = None
                self.RoSessionLocal = None

    def _run(self, fn):
        """执行数据库操作，连接异常（断连/超时）时自动重建连接并重试一次。

        修复：此前引擎实例在断连后不被重置，所有后续请求持续报 SQL 错误，
        直到进程重启。现在捕获 OperationalError 后丢弃旧引擎、重连、再试一次。
        """
        try:
            return fn()
        except OperationalError as e:
            logger.warning("[MySQL] 连接异常，尝试重建连接后重试：%s", e)
            try:
                for eng in (self.engine, self.ro_engine):
                    try:
                        if eng is not None:
                            eng.dispose()
                    except Exception:
                        pass
                self.engine = None
                self.SessionLocal = None
                self.ro_engine = None
                self.RoSessionLocal = None
                self._connect()
            except Exception as recon_err:  # noqa: BLE001
                logger.error("[MySQL] 重建连接失败：%s", recon_err)
                raise
            return fn()

    def _session(self, readonly: bool = False):
        maker = self.RoSessionLocal if (readonly and self.RoSessionLocal is not None) else self.SessionLocal
        return maker()

    def get_db(self):
        """获取数据库会话（可写，供账号/运维等写路径使用）。"""
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def execute_sql(self, sql: str, max_rows: int = 10, params: Optional[dict] = None) -> dict:
        """只读查询：仅允许SELECT，面向业务查询。params 为 SQLAlchemy 绑定参数。"""
        clean_sql = sql.strip().replace("```sql", "").replace("```", "").strip()
        logger.info(f"📝 执行查询SQL：{clean_sql[:100]}")

        if not clean_sql.upper().startswith("SELECT"):
            return {"success": False, "error": "仅支持SELECT查询"}

        def _do():
            # 读查询优先走只读账号（若已配置且连通），否则退回主账号
            db = self._session(readonly=True)
            try:
                result = db.execute(text(clean_sql), params or {})
                data = [dict(row) for row in result.mappings()]
                cap = max_rows if max_rows > 0 else len(data)
                return {
                    "success": True,
                    "data": data[:cap],
                    "count": len(data)
                }
            finally:
                db.close()

        try:
            return self._run(_do)
        except Exception as e:
            error_msg = f"SQL执行失败：{str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

    def execute_write(self, sql: str) -> dict:
        """写入操作：支持INSERT/UPDATE/DELETE，面向初始化/运维脚本。

        安全闸门：默认禁止一切写入（最小权限）。只有显式设置环境变量
        ``MYSQL_ALLOW_WRITE=1`` 时才放行，避免「通用写函数 + 有写权限账号」
        在纵深防御上留下缺口。查询路径（execute_sql）不受此开关影响。
        """
        if (os.getenv("MYSQL_ALLOW_WRITE", "") or "").strip() != "1":
            logger.warning("[MySQL] 写入被拒绝：未启用 MYSQL_ALLOW_WRITE（默认最小权限）")
            return {"success": False, "error": "写入未启用（需 MYSQL_ALLOW_WRITE=1）"}

        clean_sql = sql.strip().replace("```sql", "").replace("```", "").strip()
        logger.info(f"📝 执行写入SQL：{clean_sql[:100]}")

        allow_prefixes = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")
        if not any(clean_sql.upper().startswith(p) for p in allow_prefixes):
            return {"success": False, "error": "仅支持INSERT/UPDATE/DELETE等写入操作"}

        def _do():
            db = next(self.get_db())
            try:
                result = db.execute(text(clean_sql))
                db.commit()
                return {
                    "success": True,
                    "affected_rows": result.rowcount
                }
            finally:
                db.close()

        try:
            return self._run(_do)
        except Exception as e:
            error_msg = f"写入执行失败：{str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}


# 单例模式
mysql_client = None

def get_mysql_client():
    global mysql_client
    if mysql_client is None:
        mysql_client = MySQLClient()
    return mysql_client


if __name__ == "__main__":
    client = get_mysql_client()
    res = client.execute_sql("SELECT * FROM gene LIMIT 5")
    print(res)