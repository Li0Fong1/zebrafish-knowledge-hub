import warnings
warnings.filterwarnings("ignore")

from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from common_utils import logger
from config import DB_CONFIG


class MySQLClient:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._connect()

    def _connect(self):
        """建立MySQL连接"""
        try:
            db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}&connect_timeout=5"
            self.engine = create_engine(
                db_url,
                echo=False,
                pool_size=5,
                pool_recycle=3600
            )
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("[成功] MySQL连接成功")
        except Exception as e:
            logger.error(f"[错误] MySQL连接失败：{e}")
            raise

    def get_db(self):
        """获取数据库会话"""
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

        db = next(self.get_db())
        try:
            result = db.execute(text(clean_sql), params or {})
            data = [dict(row) for row in result.mappings()]
            cap = max_rows if max_rows > 0 else len(data)
            return {
                "success": True,
                "data": data[:cap],
                "count": len(data)
            }
        except Exception as e:
            error_msg = f"SQL执行失败：{str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        finally:
            db.close()

    def execute_write(self, sql: str) -> dict:
        """写入操作：支持INSERT/UPDATE/DELETE，面向初始化/运维脚本"""
        clean_sql = sql.strip().replace("```sql", "").replace("```", "").strip()
        logger.info(f"📝 执行写入SQL：{clean_sql[:100]}")

        allow_prefixes = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")
        if not any(clean_sql.upper().startswith(p) for p in allow_prefixes):
            return {"success": False, "error": "仅支持INSERT/UPDATE/DELETE等写入操作"}

        db = next(self.get_db())
        try:
            result = db.execute(text(clean_sql))
            db.commit()
            return {
                "success": True,
                "affected_rows": result.rowcount
            }
        except Exception as e:
            db.rollback()
            error_msg = f"写入执行失败：{str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        finally:
            db.close()


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