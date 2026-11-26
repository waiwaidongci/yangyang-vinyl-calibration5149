from enum import StrEnum


class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'


class RunStatus(StrEnum):
    COMPLETED = 'completed'
    FAILED = 'failed'


class Decision(StrEnum):
    APPROVED = 'approved'
    REJECTED = 'rejected'


class ComparisonStatus(StrEnum):
    ACTIVE = 'active'                    # 结果有效，可签发换版建议
    RECALCULATING = 'recalculating'      # 标签撤回后复算期间，暂停换版与签发
    INVALIDATED = 'invalidated'          # 已被撤回触发的复算取代，历史结果仅留档
    INSUFFICIENT = 'insufficient'        # 复算后样本达不到原门槛，证据不足


class SignoffStatus(StrEnum):
    EFFECTIVE = 'effective'              # 签发有效
    SUPERSEDED = 'superseded'            # 所依据的结果已被复算取代，留档不可再用
