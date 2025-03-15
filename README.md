# Django 다양한 기능 도전 Project

이 프로젝트는 Django와 PostgreSQL을 사용하여 구현된 텍스트 검색 벡터(Search Vector) 기능과 데이터베이스 연결 풀링(Connection Pool)을 테스트하는 애플리케이션입니다.

## 환경 설정

### 기술 스택
- Python 3.11
- Django 5.1.x
- PostgreSQL
- psycopg 3.2.6 (binary, pool 확장 포함)
- Django REST Framework
- drf-spectacular (API 문서화)

### 시스템 요구사항
- macOS (arm64 아키텍처)
- PostgreSQL 서버

## 주요 기능 및 구현 내용

### 1. PostgreSQL 연결 풀링 (Connection Pooling)

#### 문제 상황
1. Locust를 사용한 부하 테스트 중 PostgreSQL "too many clients already" 오류 발생
2. 기본 Django 데이터베이스 연결 방식은 동시에 많은 요청을 처리할 때 연결 한계에 도달
3. 연결 생성/해제의 오버헤드로 인한 성능 저하

#### 해결 방법
`utils/database_wrapper/base.py`에 Django의 기본 PostgreSQL 래퍼를 확장한 커스텀 데이터베이스 래퍼를 구현했습니다:

```python
# 주요 기능
class DatabaseWrapper(PostgresDatabaseWrpper):
    # 연결 풀 관리를 위한 클래스 레벨 변수들
    _connection_pools = {}
    _pool_settings = {}
    _pool_monitor = None
    _lock = threading.RLock()
    
    # 다양한 설정을 통한 풀 구성
    def __init__(self, settings_dict, alias=None):
        self.min_connections = settings_dict.get('MIN_CONNECTIONS', 20)
        self.max_connections = settings_dict.get('MAX_CONNECTIONS', 100)
        self.max_overflow = settings_dict.get('MAX_OVERFLOW', 10)
        # ...
```

#### 주요 구현 기능
- **연결 풀 관리**: psycopg의 ConnectionPool을 사용하여 연결 재사용
- **자동 모니터링**: 백그라운드 스레드를 통한 풀 상태 모니터링 및 관리
- **재시도 메커니즘**: 연결 실패 시 지수 백오프(exponential backoff) 적용 재시도
- **연결 건강 검사**: 반환된 연결의 유효성 검사 및 손상된 연결 처리
- **부하 감시**: 과부하 상태 감지 및 노후화된 풀 재생성

### 2. 텍스트 검색 벡터 (Search Vector)

#### 문제 상황
1. 대량의 텍스트 데이터에서 효율적인 검색 필요
2. JSON 형식으로 저장된 콘텐츠에 대한 검색 지원 필요
3. 관련성에 기반한 검색 결과 정렬 요구

#### 해결 방법
`posts/models.py`에서 PostgreSQL의 전문 검색 기능을 활용하여 구현:

```python
class Post(models.Model):
    title = models.CharField(max_length=120)
    content = models.TextField()
    date_posted = models.DateTimeField(auto_now_add=True)
    content_search = SearchVectorField(null=True)

    def save(self, *args, **kwargs):
        # JSON 콘텐츠를 문자열로 변환하여 검색 가능하게 만듦
        json_content = json.loads(self.content)
        content_string = str(json_content)
        
        # SearchVector를 사용하여 검색 벡터 생성
        self.content_search = SearchVector(
            functions.Cast(models.Value(content_string),
                           output_field=models.TextField())
        )
        return super(Post, self).save(*args, **kwargs)
```

#### 주요 구현 기능
- **SearchVectorField**: 전문 검색을 위한 특수 필드 사용
- **자동 벡터 생성**: 저장 시 검색 벡터 자동 생성
- **JSON 데이터 처리**: JSON 형식의 데이터를 검색 가능한 형태로 변환
- **Type Casting**: Django의 functions.Cast를 사용하여 데이터 타입 변환

## 설치 및 실행 방법

1. 저장소 클론
```bash
git clone [repository-url]
cd SearchVectorTest
```

2. 가상환경 설정 및 의존성 설치
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"  # 개발 의존성 포함 설치
```

3. PostgreSQL 설정
```bash
# PostgreSQL 서버 실행 필요
createdb postgres
```

4. 마이그레이션 및 서버 실행
```bash
python manage.py migrate
python manage.py runserver
```

## 데이터베이스 설정

`settings.py`에서 다음과 같이 설정합니다:

```python
DATABASES = {
    'default': {
        'ENGINE': 'utils.database_wrapper',
        'NAME': 'postgres',
        'USER': 'postgres',
        'PASSWORD': '1234',
        'HOST': 'localhost',
        'PORT': '5432',
        'MIN_CONNECTIONS': 20,     # 풀의 최소 연결 수
        'MAX_CONNECTIONS': 100,    # 풀의 최대 연결 수
        'MAX_OVERFLOW': 10,        # 최대 초과 연결 허용량
        'POOL_TIMEOUT': 3000,      # 연결 획득 최대 대기 시간(밀리초)
        'POOL_MAX_CONN_AGE': 1800, # 풀 최대 수명(초)
        'CONN_HEALTH_CHECKS': True # 연결 건강 검사 활성화
    }
}
```

## 부하 테스트

Locust를 사용하여 부하 테스트를 수행할 수 있습니다:

```bash
# 개발 의존성으로 설치
pip install -e ".[dev]"

# Locust 실행
locust -f locustfile.py --host=http://localhost:8000
```

부하 테스트 중 "too many clients already" 오류 발생 시 해결 방법:
1. PostgreSQL `max_connections` 설정 증가
2. `MIN_CONNECTIONS`, `MAX_CONNECTIONS` 값 조정
3. Locust 테스트에서 사용자 수와 생성 속도 제한

## API 문서

API 문서는 서버 실행 후 다음 URL에서 확인할 수 있습니다:
```
http://localhost:8000/api/schema/swagger-ui/
```