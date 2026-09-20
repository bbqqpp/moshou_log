import pytest

from app.wow import CLASS_MAP, SPEC_ID_MAP, localize_role, localize_spec_id


@pytest.mark.parametrize(
    ("spec_id", "expected_label"),
    [
        (104, "守护德鲁伊"),
        (73, "防护战士"),
        (264, "恢复萨满祭司"),
        (257, "神圣牧师"),
        (252, "邪恶死亡骑士"),
        (62, "奥术法师"),
        (266, "恶魔学识术士"),
        (65, "神圣圣骑士"),
    ],
)
def test_localizes_known_spec_ids(spec_id, expected_label):
    assert localize_spec_id(spec_id)["label"] == expected_label


def test_unknown_spec_id_is_left_empty_rather_than_guessed():
    """认不出的 ID 必须留空：报错专精比没有专精更糟，报告会据此推断减伤技能。"""
    for unknown in (999999, None, "not-a-number", ""):
        assert localize_spec_id(unknown) == {"class": "", "spec": "", "label": ""}


def test_spec_id_map_covers_every_class():
    covered = {class_key for class_key, _ in SPEC_ID_MAP.values()}
    assert covered == set(CLASS_MAP)


def test_localize_role_cannot_detect_spec_from_wcl_type_field():
    """WCL tables 的 type 字段给的是职业名而非专精，所以专精永远判不出来。

    这是 _build_actor_catalog 必须用 combatant_info 的 specID 覆盖它的原因。
    """
    role = localize_role("Shaman")
    assert role["class"] == "萨满祭司"
    assert role["spec"] == ""
    assert role["label"] == "萨满祭司"


def test_localize_role_still_works_for_real_spec_strings():
    """旧路径对真正带专精的字符串仍然有效，作为没有 specID 时的回退。"""
    assert localize_role("Restoration Shaman")["label"] == "恢复萨满祭司"
