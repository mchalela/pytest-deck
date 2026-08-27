"""pytest-django — the collect-recovery demo for a structural plugin switch.

This file imports an ORM model at MODULE level, so with the "Django
(pytest-django)" switch off it doesn't even collect: django.setup() never ran,
and the import raises ImproperlyConfigured. On a cold deck this whole file
shows up as an error strip in the tree. Flip the django switch (the deck
re-collects with ``-p django``), the error strip disappears, and the tests
appear and run green. Needs ``pip install pytest-django django``; the switch
is hidden until it's installed.

``DJANGO_SETTINGS_MODULE`` is provided as an ini key in examples/pytest.ini —
pytest-django's documented mechanism, and the deck honors the user's ini.
Without the plugin loaded that key is just an unknown ini option: pytest emits
a PytestConfigWarning and moves on (examples/pytest.ini has no
``filterwarnings = error``, so it never turns fatal). The settings package
(``mysite``) and the ``polls`` app are importable via the ini
``pythonpath = django_app`` key, which the deck merges into its own
import-path injection. The database is in-memory sqlite — nothing touches
the disk.
"""

import pytest

from polls.models import Question  # module-level ORM import; needs django.setup()


@pytest.mark.django_db
def test_create_and_count():
    Question.objects.create(text="Is the deck on?")
    assert Question.objects.count() == 1
    assert Question.objects.get(text="Is the deck on?").votes == 0


def test_str_without_touching_the_db():
    # An unsaved instance: exercises the model class, needs no database.
    q = Question(text="unsaved")
    assert str(q) == "unsaved"
