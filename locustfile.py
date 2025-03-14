from locust import task, TaskSet, HttpUser, between


class UserBehavior(TaskSet):
    @task(1)
    def get_user_detail(self):
        self.client.get(f'/posts/')


class LocustUser(HttpUser):
    host = "http://localhost:8000"
    tasks = [UserBehavior]
    wait_time = between(1, 4)
