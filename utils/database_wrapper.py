from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrpper


from psycopg2 import pool
import threading


class DatabaseWrapper(PostgresDatabaseWrpper):
    # Class-level pool storage
    _pools = {}
    _pool_lock = threading.RLock()

    def __init__(self, settings_dict, alias=None):
        super().__init__(settings_dict, alias)
        self.min_connections = settings_dict.get('MIN_CONNECTIONS', 1)
        self.max_connections = settings_dict.get('MAX_CONNECTIONS', 10)
        self.max_overflow = settings_dict.get('MAX_OVERFLOW', 5)
        self.pool = None

    def get_new_connection(self, conn_params):
        with self._pool_lock:
            alias = self.alias
            if alias not in self._pools:
                self._pools[alias] = pool.ThreadedConnectionPool(
                    minconn=self.min_connections,
                    maxconn=self.max_connections + self.max_overflow,
                    **conn_params
                )
            self.pool = self._pools[alias]

        # 연결을 가져오기 전에 유효성 검사 추가
        connection = self.pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            # 연결이 유효하지 않으면 새 연결 요청
            self.pool.putconn(connection, close=True)
            connection = self.pool.getconn()
        return connection

    def close(self):
        if self.connection is not None:
            with self.wrap_database_errors:
                if self.pool:
                    # Return the connection to the pool
                    self.pool.putconn(self.connection)
                    # Connection can no longer be used directly
                    self.connection = None
                else:
                    return self.connection.close()

    @classmethod
    def close_all_pools(cls):
        """Close all connection pools."""
        with cls._pool_lock:
            for alias, pool_instance in cls._pools.items():
                pool_instance.closeall()
            cls._pools.clear()

    @classmethod
    def get_pool_status(cls, alias=None):
        """풀 상태 정보 반환 (사용 중인 연결, 가용 연결 등)"""
        with cls._pool_lock:
            if alias:
                if alias in cls._pools:
                    pool_instance = cls._pools[alias]
                    return {
                        'used_connections': len(pool_instance._used),
                        'available_connections': len(pool_instance._pool),
                        'maxconn': pool_instance.maxconn,
                    }
            else:
                return {a: cls.get_pool_status(a) for a in cls._pools}