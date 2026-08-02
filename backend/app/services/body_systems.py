from __future__ import annotations

import re
from collections.abc import Iterable


BODY_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("digestive_gut", "消化系统/肠道"),
    ("liver_detox", "肝脏/解毒系统"),
    ("immune_inflammation", "免疫/炎症系统"),
    ("endocrine_metabolic", "内分泌/代谢系统"),
    ("cardiovascular", "心血管系统"),
    ("respiratory", "呼吸系统"),
    ("neuro_sleep", "神经/认知/睡眠系统"),
    ("bone_muscle", "骨骼/肌肉系统"),
    ("urinary_renal", "泌尿/肾脏系统"),
    ("reproductive_breast", "生殖/妇科/乳腺系统"),
    ("skin_mucosa", "皮肤/黏膜系统"),
)

SYSTEM_NAMES = dict(BODY_SYSTEMS)
SYSTEM_NAME_TO_ID = {name: system_id for system_id, name in BODY_SYSTEMS}

AXIS_SYSTEM_MAP: dict[str, tuple[str, ...]] = {
    "gut_bile": ("digestive_gut", "liver_detox"),
    "gut_microbiome": ("digestive_gut",),
    "gut_mucosa": ("digestive_gut", "skin_mucosa"),
    "gastric_acid": ("digestive_gut",),
    "digestive_enzyme": ("digestive_gut",),
    "liver_detox": ("liver_detox",),
    "methylation": ("liver_detox", "cardiovascular"),
    "immune": ("immune_inflammation",),
    "inflammation": ("immune_inflammation",),
    "antioxidant": ("immune_inflammation",),
    "thyroid_axis": ("endocrine_metabolic", "immune_inflammation"),
    "glycemic_balance": ("endocrine_metabolic",),
    "weight_metabolism": ("endocrine_metabolic",),
    "nutrition_repletion": ("endocrine_metabolic", "digestive_gut", "bone_muscle"),
    "vitamin_d_repletion": ("bone_muscle", "immune_inflammation"),
    "hormone_axis": ("endocrine_metabolic", "reproductive_breast"),
    "female_hormone": ("reproductive_breast",),
    "cardiovascular": ("cardiovascular",),
    "lipid_balance": ("cardiovascular", "endocrine_metabolic"),
    "sleep_stress": ("neuro_sleep",),
    "neuro_cognitive": ("neuro_sleep",),
    "energy_mitochondria": ("neuro_sleep", "bone_muscle"),
    "bone_metabolism": ("bone_muscle",),
    "iron_repletion": ("endocrine_metabolic",),
    "foundational": ("endocrine_metabolic",),
    "anti_aging": ("immune_inflammation",),
}

_SYSTEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "digestive_gut": (
        "肠道", "肠胃", "胃", "胃炎", "肠炎", "肠漏", "菌群", "腹胀", "腹泻", "便秘", "消化",
        "钙卫蛋白", "zonulin", "siga", "β-葡萄糖醛酸酶", "拟杆菌", "乳酸杆菌",
    ),
    "liver_detox": ("肝", "肝脏", "脂肪肝", "胆", "胆汁", "解毒", "谷胱甘肽", "alt", "ast", "ggt", "尿酸"),
    "immune_inflammation": (
        "免疫", "炎症", "过敏", "抗体", "超敏c反应蛋白", "hs-crp", "crp", "ige", "iga", "白细胞",
        "维生素d", "25-oh",
    ),
    "endocrine_metabolic": (
        "内分泌", "代谢", "血糖", "胰岛素", "糖化", "甲状腺", "tsh", "ft3", "ft4", "泌乳素",
        "lh/fsh", "体重", "bmi", "铁蛋白", "血清铁", "血红蛋白",
    ),
    "cardiovascular": (
        "心血管", "心电", "心律", "血压", "胆固醇", "甘油三酯", "ldl", "hdl", "载脂蛋白",
        "同型半胱氨酸", "hcy",
    ),
    "respiratory": ("呼吸", "肺", "肺结节", "lung-rads", "咳嗽", "哮喘", "弥散功能"),
    "neuro_sleep": ("神经", "认知", "睡眠", "失眠", "早醒", "脑雾", "焦虑", "抑郁", "头痛", "疲劳", "压力"),
    "bone_muscle": (
        "骨", "骨量", "骨密度", "骨质", "肌肉", "关节", "维生素d", "25-oh",
        "脊柱", "脊椎", "颈椎", "胸椎", "腰椎", "骶椎", "椎间盘", "椎管",
    ),
    "urinary_renal": ("泌尿", "肾", "肾脏", "肾囊肿", "尿检", "尿液", "肌酐", "尿素氮"),
    "reproductive_breast": ("生殖", "妇科", "乳腺", "乳房", "卵巢", "子宫", "阴道", "月经", "宫颈", "birads", "bi-rads"),
    "skin_mucosa": ("皮肤", "黏膜", "湿疹", "皮炎", "皮疹", "脱发", "头发", "伤口", "口腔"),
}

_MEANING: dict[str, str] = {
    "digestive_gut": "这些线索可能涉及消化吸收、肠黏膜屏障、局部炎症或菌群结构变化，但不能仅凭单项结果作确定诊断",
    "liver_detox": "这些线索可能反映肝胆代谢、胆汁利用及生物转化负担，需要结合生活方式和复查趋势综合判断",
    "immune_inflammation": "这些线索可能提示免疫反应或炎症负担增加，应结合症状、过敏史和其他检查进行解释",
    "endocrine_metabolic": "这些线索可能影响血糖、体重、甲状腺、激素或基础营养代谢，需要观察多指标之间的一致性",
    "cardiovascular": "这些线索可能涉及血脂、血压、循环或血管炎症风险，需结合家族史和临床评估判断",
    "respiratory": "这些线索可能影响肺部结构或呼吸功能，应以影像随访、肺功能和临床症状为主要判断依据",
    "neuro_sleep": "这些线索可能与睡眠节律、压力恢复、认知状态或神经调节有关，需要结合日常表现持续观察",
    "bone_muscle": "这些线索可能涉及骨代谢、肌肉状态或运动耐受，需结合维生素D、骨密度和活动情况判断",
    "urinary_renal": "这些线索可能涉及肾脏结构、滤过功能或泌尿系统状态，应结合肾功能和影像复查判断",
    "reproductive_breast": "这些线索可能涉及女性激素节律、生殖系统或乳腺状态，应结合专科检查和随访结果判断",
    "skin_mucosa": "这些线索可能反映皮肤与黏膜屏障、免疫或营养状态变化，需要结合症状和触发因素判断",
}

_PRIORITY_REASON: dict[str, str] = {
    "digestive_gut": "消化与肠道状态会影响营养吸收、食物耐受和全身炎症管理，因此在首月方案中优先处理",
    "liver_detox": "肝胆代谢会影响脂质处理和整体恢复，输入负担持续时可能降低后续干预效率",
    "immune_inflammation": "持续的免疫或炎症负担可能牵动多个身体系统，需要先减少触发因素并观察趋势",
    "endocrine_metabolic": "内分泌与代谢状态会影响能量、体重和营养利用，是首月确定干预先后的重要依据",
    "cardiovascular": "心血管相关线索涉及长期风险管理，应与饮食、运动和复查安排同步推进",
    "respiratory": "呼吸系统异常需要以临床随访为先，并避免在证据不足时扩大营养干预结论",
    "neuro_sleep": "睡眠与压力恢复会直接影响执行力、代谢和免疫调节，是其他干预生效的基础",
    "bone_muscle": "骨骼肌肉状态影响活动能力和长期恢复，需要与营养状态及运动计划同步管理",
    "urinary_renal": "肾脏和泌尿状态关系到补充剂安全性，应在方案执行前确认风险和复查节奏",
    "reproductive_breast": "生殖、妇科和乳腺问题需要与激素节律及专科随访结合，不宜脱离原始证据解释",
    "skin_mucosa": "皮肤黏膜表现可作为免疫、营养和屏障状态的外在信号，适合纳入阶段性观察",
}

_INTERVENTION: dict[str, str] = {
    "digestive_gut": "先记录排便、腹胀和触发食物，优化饮食结构与消化支持，再根据耐受和复查结果调整",
    "liver_detox": "先减少酒精、高油外食和不必要暴露，配合规律作息，再评估肝胆代谢支持",
    "immune_inflammation": "优先稳定睡眠、饮食和过敏触发管理，并结合炎症及免疫指标复查",
    "endocrine_metabolic": "优先调整餐盘结构、饭后活动、睡眠节律和基础营养，再观察代谢指标变化",
    "cardiovascular": "控制精制碳水和不良油脂，增加规律活动，并按计划复查血脂、血压等指标",
    "respiratory": "按医嘱完成影像或肺功能随访，同时减少烟草及刺激性环境暴露",
    "neuro_sleep": "固定起床时间，减少夜间刺激和下午咖啡因，记录睡眠与白天精力变化",
    "bone_muscle": "结合蛋白质摄入、渐进抗阻活动和骨代谢复查，稳妥安排相关营养支持",
    "urinary_renal": "先核对肾功能、用药和饮水情况，再决定补充剂选择及随访频率",
    "reproductive_breast": "以专科随访为基础，结合压力、睡眠和体重管理观察周期及相关症状",
    "skin_mucosa": "记录皮肤黏膜变化和触发因素，优化饮食、睡眠与屏障支持，并观察恢复趋势",
}


def priority_level(score: float) -> str:
    if score >= 85:
        return "最高优先级"
    if score >= 60:
        return "优先级高"
    return "中度关注"


def system_ids_for_axes(axes: Iterable[str]) -> list[str]:
    result: list[str] = []
    for axis in axes:
        result.extend(AXIS_SYSTEM_MAP.get(str(axis).strip(), ()))
    return list(dict.fromkeys(result))


def classify_text_to_system_ids(*values: str | None) -> list[str]:
    text = re.sub(r"\s+", "", " ".join(str(value or "") for value in values)).lower()
    matched = [
        system_id
        for system_id, keywords in _SYSTEM_KEYWORDS.items()
        if any(keyword.lower() in text for keyword in keywords)
    ]
    return matched


def normalize_legacy_system_id(text: str) -> str | None:
    normalized = (text or "").strip()
    for system_id, system_name in BODY_SYSTEMS:
        if system_name in normalized:
            return system_id
    aliases = {
        "脑肠轴": "neuro_sleep",
        "线粒体": "neuro_sleep",
        "氧化压力": "immune_inflammation",
        "生物转化": "liver_detox",
        "解毒": "liver_detox",
        "甲状腺": "endocrine_metabolic",
        "造血": "endocrine_metabolic",
        "铁储备": "endocrine_metabolic",
        "女性激素": "reproductive_breast",
        "乳腺": "reproductive_breast",
        "肠道": "digestive_gut",
        "消化": "digestive_gut",
        "免疫": "immune_inflammation",
        "炎症": "immune_inflammation",
    }
    return next((system_id for keyword, system_id in aliases.items() if keyword in normalized), None)


def build_system_summary(system_id: str, evidence_names: Iterable[str], score: float) -> str:
    names = list(dict.fromkeys(str(item).strip() for item in evidence_names if str(item).strip()))[:6]
    evidence = "、".join(names) if names else "已确认异常、症状及报告结论"
    system_name = SYSTEM_NAMES.get(system_id, "相关身体系统")
    return (
        f"发现：{evidence}提示{system_name}需要关注。"
        f"含义：{_MEANING.get(system_id, '这些线索需要结合原始报告、症状和复查趋势综合解释')}。"
        f"优先原因：{_PRIORITY_REASON.get(system_id, '该系统与当前核心问题及整体恢复相关')}。"
        f"干预方向：{_INTERVENTION.get(system_id, '先从生活方式和基础营养入手，再根据耐受与复查结果调整')}。"
    )
