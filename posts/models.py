import json

from django.db import models
from django.db.models import functions
from django.contrib.postgres.search import SearchVector, SearchVectorField

# Create your models here.

class Post(models.Model):
    title = models.CharField(max_length=120)
    content = models.TextField()
    date_posted = models.DateTimeField(auto_now_add=True)
    content_search = SearchVectorField(
        null=True
    )

    def __str__(self):
        return self.title

    """
    데이터를 변환해서 넣고 싶으면 functions.Cast를 사용하면 된다.
    """
    def save(self, *args, **kwargs):

        json_content = json.loads(self.content)

        content_string = str(json_content)

        self.content_search = SearchVector(
                    functions.Cast(models.Value(content_string),
                                   output_field=models.TextField())
                )

        return super(Post, self).save(*args, **kwargs)