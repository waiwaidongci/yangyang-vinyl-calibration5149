from fastapi import APIRouter
from fastapi.responses import Response

from src.controllers.dependencies import Actor, Admin, Db, Writer
from src.domain.version_records import COLUMNS, FIELDS, VERSION_LABELS
from src.schemas.versioning import (
    BatchInfo, LabelWithdrawal, SignoffInfo, VersionBatchCreate, VersionDatasetImport, VersionSignoffCreate,
)
from src.services import versioning
from src.utils.csv_files import write_rows

router = APIRouter(prefix='/versioning')


@router.get('/spec')
def versioning_spec(actor: Actor):
    return {
        'columns': COLUMNS,
        'fields': FIELDS,
        'versions': VERSION_LABELS,
        'holdout_fraction': {'default': 0.25, 'min': 0.15, 'max': 0.40},
        'gate': {'min_train': 8, 'min_holdout': 4},
        'switch_rule': '同一批按档号留出的确认片段上，两版各自做保序校准；仅当新版校准后 Brier 严格更低才建议换版。',
        'withdrawal_rule': '人工标签撤回后受影响的换版结果失效重算；复算期间暂停换版与签发；样本达不到原门槛标记为证据不足。',
    }


@router.post('/datasets', status_code=201)
def import_dataset(payload: VersionDatasetImport, session: Db, actor: Writer):
    return versioning.import_version_dataset(session, payload.name, payload.csv, actor.id)


@router.get('/batches', response_model=list[BatchInfo])
def list_batches(session: Db, actor: Actor):
    return versioning.list_batches(session, actor)


@router.post('/batches', response_model=BatchInfo, status_code=201)
def create_batch(payload: VersionBatchCreate, session: Db, actor: Writer):
    return versioning.create_batch(session, payload, actor)


@router.get('/batches/{batch_id}')
def batch_detail(batch_id: int, session: Db, actor: Actor):
    batch, signoff = versioning.get_batch(session, batch_id, actor)
    return {**versioning.batch_info(batch, signoff), 'result': batch.result, 'error': batch.error}


@router.post('/batches/{batch_id}/withdraw', response_model=BatchInfo, status_code=201)
def withdraw(batch_id: int, payload: LabelWithdrawal, session: Db, actor: Writer):
    return versioning.withdraw_labels(session, batch_id, payload, actor)


@router.post('/batches/{batch_id}/signoff', response_model=SignoffInfo, status_code=201)
def signoff(batch_id: int, payload: VersionSignoffCreate, session: Db, actor: Admin):
    return versioning.signoff(session, batch_id, payload, actor)


@router.get('/signoffs', response_model=list[SignoffInfo])
def signoffs(session: Db, actor: Admin):
    return versioning.list_signoffs(session, actor)


@router.get('/batches/{batch_id}/export/{key}')
def export(batch_id: int, key: str, session: Db, actor: Actor):
    table = versioning.export_table(session, batch_id, key, actor)
    return Response(
        write_rows(table['columns'], table['rows']), media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="version-batch-{batch_id}-{table["key"]}.csv"'},
    )
