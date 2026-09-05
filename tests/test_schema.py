"""Guards against the ORM and the migrations disagreeing on the schema.

The rest of the suite builds tables from Base.metadata, so it can never catch a
divergence between what the model persists and what the migration actually
creates in a real database. This did bite once: the paymentstate type is created
by the migration with the enum values ("received"), but the ORM defaulted to
persisting member names ("RECEIVED"), which the live type rejected. These tests
compare the two directly so that divergence fails here rather than in production.
"""

import importlib.util
import pathlib

from app.models import payment_state
from app.states import PaymentState

MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _load(filename):
    path = MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_orm_persists_enum_values_not_names():
    # The labels SQLAlchemy will send to the database must be the lowercase
    # values, so an INSERT is accepted by the paymentstate type.
    assert list(payment_state.enums) == [m.value for m in PaymentState]


def test_orm_enum_matches_initial_migration():
    initial = _load("001_initial.py")
    assert list(payment_state.enums) == list(initial.PAYMENT_STATE.enums)
    assert payment_state.name == initial.PAYMENT_STATE.name
