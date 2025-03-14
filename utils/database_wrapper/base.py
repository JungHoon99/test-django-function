from django.core.exceptions import ImproperlyConfigured
from django.db.backends.base.base import NO_DB_ALIAS
from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrpper
import logging
import time
import threading
from datetime import datetime, timedelta


class DatabaseWrapper(PostgresDatabaseWrpper):
    # Class-level pool storage and settings
    _connection_pools = {}
    _pool_settings = {}
    _pool_monitor = None
    _lock = threading.RLock()

    def __init__(self, settings_dict, alias=None):
        super().__init__(settings_dict, alias)
        self.min_connections = settings_dict.get('MIN_CONNECTIONS', 20)
        self.max_connections = settings_dict.get('MAX_CONNECTIONS', 100)  # Reduced default
        self.max_overflow = settings_dict.get('MAX_OVERFLOW', 10)  # Reduced default
        self.timeout = settings_dict.get('POOL_TIMEOUT', 3000)
        self.max_conn_age = settings_dict.get('POOL_MAX_CONN_AGE', 1800)  # 30 minutes
        self.recycle_threshold = settings_dict.get('POOL_RECYCLE_THRESHOLD', 0.8)  # 80%

        # Store settings for this alias
        if alias and alias != NO_DB_ALIAS:
            DatabaseWrapper._pool_settings[alias] = {
                'min_connections': self.min_connections,
                'max_connections': self.max_connections,
                'max_overflow': self.max_overflow,
                'timeout': self.timeout,
                'created_at': datetime.now()
            }

            # Start monitor thread if not already running
            if DatabaseWrapper._pool_monitor is None:
                DatabaseWrapper._start_pool_monitor()

    @classmethod
    def _start_pool_monitor(cls):
        """Start a background thread to monitor connection pools"""

        def monitor_pools():
            while True:
                try:
                    cls._check_all_pools()
                except Exception as e:
                    logging.error(f"Error in pool monitor: {e}")
                time.sleep(300)  # Check every 5 minutes

        cls._pool_monitor = threading.Thread(
            target=monitor_pools,
            daemon=True,
            name="db-pool-monitor"
        )
        cls._pool_monitor.start()

    @classmethod
    def _check_all_pools(cls):
        """Check all pools for potential issues and clean up if needed"""
        now = datetime.now()
        with cls._lock:
            for alias, settings in cls._pool_settings.items():
                if alias in cls._connection_pools:
                    pool = cls._connection_pools[alias]
                    # Check if pool is overloaded
                    if pool.busy > settings['max_connections'] * cls._pool_settings[alias]['recycle_threshold']:
                        logging.warning(f"Pool {alias} is nearing capacity, recycling connections")
                        try:
                            pool.resize(settings['min_connections'])
                        except Exception as e:
                            logging.error(f"Failed to resize pool {alias}: {e}")

                    # Check pool age
                    age = now - settings.get('created_at', now)
                    if age > timedelta(seconds=settings.get('max_conn_age', 1800)):
                        logging.info(f"Pool {alias} has reached max age, recreating")
                        try:
                            pool.close()
                            del cls._connection_pools[alias]
                        except Exception as e:
                            logging.error(f"Error closing aged pool {alias}: {e}")

    @property
    def pool(self):
        """Get or create a connection pool for this database alias"""
        pool_options = self.settings_dict["OPTIONS"].get("pool")
        if self.alias == NO_DB_ALIAS or not pool_options:
            return None

        with DatabaseWrapper._lock:
            if self.alias not in self._connection_pools:
                if self.settings_dict.get("CONN_MAX_AGE", 0) != 0:
                    raise ImproperlyConfigured("Pooling doesn't support persistent connections.")

                # Set the default options
                if pool_options is True:
                    pool_options = {}

                try:
                    from psycopg_pool import ConnectionPool
                except ImportError as err:
                    raise ImproperlyConfigured(
                        "Error loading psycopg_pool module.\nDid you install psycopg[pool]?"
                    ) from err

                connect_kwargs = self.get_connection_params()
                connect_kwargs["autocommit"] = True
                enable_checks = self.settings_dict.get("CONN_HEALTH_CHECKS", True)

                pool = ConnectionPool(
                    kwargs=connect_kwargs,
                    open=True,
                    configure=self._configure_connection,
                    min_size=self.min_connections,
                    max_size=self.max_connections,
                    max_overflow=self.max_overflow,
                    timeout=self.timeout,
                    check=ConnectionPool.check_connection if enable_checks else None,
                    reset=True,  # Reset connections when returned to pool
                    **pool_options,
                )

                self._connection_pools[self.alias] = pool
                self._pool_settings[self.alias]['created_at'] = datetime.now()

            return self._connection_pools[self.alias]

    def get_new_connection(self, conn_params):
        """Get a connection from the pool with error handling and failover"""
        if not self.pool:
            max_retries = 10  # Maximum retry attempts
            retry_count = 0
            retry_delay = 0.01  # Initial retry delay (seconds)

            while True:
                try:
                    return super().get_new_connection(conn_params)
                except Exception as e:
                    if "too many clients already" in str(e) and retry_count < max_retries:
                        retry_count += 1
                        logging.warning(
                            f"Too many connections and no pool available: {e}. Retry {retry_count}/{max_retries}"
                        )
                        # Exponential backoff
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        # If it's not a connection limit issue or we've exceeded retries
                        raise

        try:
            conn = self.pool.getconn()
            # Validate connection before returning
            if hasattr(conn, 'closed') and not conn.closed:
                return conn
            else:
                # Connection is closed, remove it and try again
                self.pool.putconn(conn, close=True)
                return self.pool.getconn()
        except Exception as e:
            if "too many clients already" in str(e):
                # Try emergency cleanup of connections
                self._cleanup_connections()
                # Try again after cleanup
                return self.pool.getconn()
            raise

    def close(self, *args, **kwargs):
        """Return the connection to the pool instead of closing it."""
        if self.connection is None or not self.pool:
            return super().close(*args, **kwargs)

        try:
            if self.connection.closed:
                self.connection = None
                return

            # Return connection to the pool
            self.pool.putconn(self.connection)
            self.connection = None
        except Exception as e:
            logging.error(f"Error returning connection to pool: {e}")
            # Force close if we had an error
            try:
                if self.connection and not self.connection.closed:
                    self.connection.close()
            except:
                pass
            self.connection = None

    def _cleanup_connections(self):
        """Emergency cleanup of connections when pool is full"""
        if not self.pool:
            return False

        try:
            # Close and recreate pool
            current_pool = self.pool
            with DatabaseWrapper._lock:
                self._connection_pools.pop(self.alias, None)

            # Close previous pool
            try:
                current_pool.close()
            except:
                pass

            # Allow a small delay for connections to be properly closed
            time.sleep(0.5)

            # Getting pool will recreate it
            _ = self.pool
            return True
        except Exception as e:
            logging.error(f"Error during emergency connection cleanup: {e}")
            return False

    def close_if_unusable_or_obsolete(self, *args, **kwargs):
        """Modified to work with the connection pool."""
        if self.connection is None:
            return

        if not self.pool:
            return super().close_if_unusable_or_obsolete(*args, **kwargs)

        # Check if connection is usable
        try:
            if not self.is_usable():
                self.close()
        except:
            # If we can't determine if it's usable, close it to be safe
            self.close()

    def close_pool(self):
        """Close the connection pool for this alias"""
        if self.pool:
            with DatabaseWrapper._lock:
                if self.alias in self._connection_pools:
                    try:
                        self._connection_pools[self.alias].close()
                        del self._connection_pools[self.alias]
                    except Exception as e:
                        logging.error(f"Error closing pool {self.alias}: {e}")

    def get_pool_status(self):
        """Returns detailed status information about the connection pool."""
        if not self.pool:
            return {"status": "disabled"}

        return {
            "status": "active",
            "size": self.pool.size,
            "min_size": self.pool.min_size,
            "max_size": self.pool.max_size,
            "max_overflow": self.pool.max_overflow,
            "overflow": self.pool.overflow,
            "idle": self.pool.idle,
            "busy": self.pool.busy,
            "usage_percent": (self.pool.busy / self.pool.max_size * 100) if self.pool.max_size > 0 else 0,
            "created_at": self._pool_settings.get(self.alias, {}).get('created_at')
        }

    def reset_pool(self):
        """Reset the connection pool, closing idle connections."""
        if not self.pool:
            return False

        try:
            self.pool.resize(self.min_connections)
            return True
        except Exception as e:
            logging.error(f"Error resizing pool: {e}")
            return False

# from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrpper
# import logging
# import time
#
# from django.utils.asyncio import async_unsafe
#
#
# class DatabaseWrapper(PostgresDatabaseWrpper):
#
#     @async_unsafe
#     def get_new_connection(self, conn_params):
#         """Get a connection from the pool with error handling and failover"""
#         # 풀 미사용 시 재시도 로직
#         if not self.pool:
#             max_retries = 10  # 최대 재시도 횟수
#             retry_count = 0
#             retry_delay = 0.1  # 초기 대기 시간(초)
#
#             while True:
#                 try:
#                     conn = super().get_new_connection(conn_params)
#
#                     return conn
#                 except Exception as e:
#                     if "too many clients already" in str(e) and retry_count < max_retries:
#                         retry_count += 1
#                         logging.warning(
#                             f"Too many connections and no pool available: {e}. Retry {retry_count}/{max_retries}")
#                         # 지수 백오프: 점점 더 오래 대기
#                         time.sleep(retry_delay)
#                         retry_delay *= 2
#                     else:
#                         break
