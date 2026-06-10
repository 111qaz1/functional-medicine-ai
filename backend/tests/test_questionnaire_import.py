from __future__ import annotations

import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.questionnaire_import import QuestionnaireImportService


def _paragraph_xml(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _cell_xml(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def _row_xml(cells: list[str]) -> str:
    return "<w:tr>" + "".join(_cell_xml(cell) for cell in cells) + "</w:tr>"


def _table_xml(rows: list[list[str]]) -> str:
    return "<w:tbl>" + "".join(_row_xml(row) for row in rows) + "</w:tbl>"


def _build_docx_bytes(paragraphs: list[str], tables: list[list[list[str]]]) -> bytes:
    body = "".join(_paragraph_xml(text) for text in paragraphs) + "".join(_table_xml(table) for table in tables)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


class QuestionnaireImportServiceTests(unittest.TestCase):
    def test_imports_filled_msq_docx_into_questionnaire(self) -> None:
        service = QuestionnaireImportService()
        content = _build_docx_bytes(
            paragraphs=["姓名：穆 性别：女 年龄：34"],
            tables=[
                [
                    ["1. 您最近一次体检查出的主要问题？"],
                    ["桥本氏甲状腺炎，甲减，窦性心律"],
                    ["2. 您是否被诊断出患有某种慢性疾病？□ 是", "☑ 否"],
                    ["如有，请具体说明:"],
                    ["3. 针对上述慢性疾病，您是否按照医嘱正在服用药物？", "□ 是", "☑ 否"],
                    ["西药", "", "", "中药", "", ""],
                    ["名称剂量频率", "", "", "名称", "", ""],
                    [""],
                    ["4. 您是否对某些药物过敏？□ 是", "☑ 否"],
                    ["如有，请说明何种药物及症状"],
                ],
                [
                    ["", "目前正患有何种慢性疾病？", "曾经患有何种慢性疾病？", "曾经接受过何种手术？"],
                    ["您本人", "甲减、桥本氏甲状腺炎", "无", "无"],
                    ["您的父亲", "糖尿病、高血压", "脑卒中", "无"],
                    ["您的母亲", "阑尾炎", "无", "无"],
                ],
                [
                    ["1. 您的睡眠质量如何？"],
                    ["□ 很好☑ 多梦□ 早醒"],
                    ["□ 入睡困难☑ 容易入睡，但不深睡"],
                    ["原因:"],
                    ["2. 您一般什么时间上床睡觉？ 23点"],
                    ["3. 您一般早上什么时间醒来？ 6点半"],
                ],
                [
                    ["4. 您日常三餐主要食用？"],
                    ["□ 米饭占比20% □ 面食占比10% □ 红肉占比15% □ 蔬菜占比30% □ 水果占比10% □ 鱼类和海鲜占比5%"],
                    ["5. 您能够规律地吃早餐吗？"],
                    ["□ 我通常不吃早餐 ☑ 我通常在外面买早餐吃"],
                    ["6.常饮用：☑ 白开水（☑热) 每日8杯"],
                    ["7. 您喝酒吗？□ 是☑ 否"],
                    ["8. 您平时饮酒常饮用哪种酒？"],
                    ["9. 您每次喝酒的习惯？"],
                    ["10. 每周外出就餐次数？5次"],
                    ["11. 经常使用快餐或加工食物？ □ 是 ☑ 否"],
                    ["12. 对食物添加物及防腐剂敏感？ □ 是 ☑ 否"],
                ],
                [
                    ["1.您有补充营养食品吗？ □ 是 ☑ 否"],
                ],
                [
                    ["1. 您在工作日每天工作小时数？ 8 小时/日"],
                    ["2. 您每天使用电脑时间多长？6-7小时/日"],
                    ["第六部分：环境因素"],
                    ["1. 您是否经常暴露在空气污染和油烟的环境中？□ 是 ☑ 否"],
                    ["2. 对杀虫剂、香烟的烟雾、香水或自动挥发的芳香剂会明显的感到不舒服？ ☑ 是 □ 否"],
                    ["3. 附近有新铺地毯、油漆或家具散发出气味？ □ 是 ☑ 否"],
                    ["4. 平均多长时间染一次发？ 无"],
                    ["5. 长途旅行后会有时差症状？ ☑ 是 □ 否"],
                ],
                [
                    ["1. 有运动习惯？ □ 是 ☑ 否"],
                    ["2. 每周运动量常保持在三次以上，且每次至少有20分钟？ □ 是 ☑ 否"],
                    ["3. 经常练习任何舒解压力的活动、瑜伽或静坐 ☑ 是 □ 否"],
                ],
                [
                    ["1. 初次月经年龄:"],
                    ["2. 月经周期: 30 天"],
                    ["4. 每次月经持续时间: 7天"],
                    ["6. 怀孕次数：1 次"],
                    ["7. 孩子数量：1 个"],
                    ["8. 生产方式：顺产"],
                ],
                [
                    ["级别序号症状描述从来没有", "偶尔", "轻微", "中等", "严重"],
                    ["", "", "0", "1", "2", "3", "4"],
                    ["1", "慢性咳嗽", "☑", "□", "□", "□", "□"],
                    ["肺/喉咙2", "喉咙痛、颈部或腋下疼痛", "☑", "□", "□", "□", "□"],
                    ["1", "消化不良", "□", "☑", "□", "□", "□"],
                    ["2", "腹胀/胀气", "□", "☑", "□", "□", "□"],
                    ["3", "胃灼热感/胃痛", "☑", "□", "□", "□", "□"],
                    ["消化功能4", "恶心或呕吐", "☑", "□", "□", "□", "□"],
                    ["5", "便秘", "□", "☑", "□", "□", "□"],
                    ["6", "腹泻", "□", "☑", "□", "□", "□"],
                    ["7", "胃酸逆流", "☑", "□", "□", "□", "□"],
                    ["3关节/肌肉", "肌肉无力或疲倦", "□", "☑", "□", "□", "□"],
                ],
                [
                    ["1、整体而言，您对自己的健康状况觉得身体感觉上：☑ 尚可"],
                    ["2、您接收检查的目的是希望：（可复选）☑ 早期发现疾病以利早期治疗 □ 找出身体不适的原因"],
                    ["3、您希望以何种方式来促进健康呢？（可多选）☑ 改变生活形态 ☑ 改变饮食习惯 ☑ 营养辅助品"],
                    ["4、最希望医生帮您解决的问题是(请依优先级):A 甲减 B CD"],
                ],
            ],
        )

        questionnaire = service.parse(
            filename="MSQ--test.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=content,
        )

        self.assertEqual(questionnaire.age, 34)
        self.assertEqual(questionnaire.sex, "female")
        self.assertIn("甲减", questionnaire.known_conditions)
        self.assertIn("桥本氏甲状腺炎", questionnaire.known_conditions)
        self.assertIn("父亲：糖尿病、高血压、脑卒中", questionnaire.family_history)
        self.assertEqual(questionnaire.sleep_hours, 7.5)
        self.assertIn("多梦", questionnaire.sleep_quality or "")
        self.assertEqual(questionnaire.dining_out_frequency, "每周5次")
        self.assertEqual(questionnaire.red_meat_intake_ratio, "15%")
        self.assertEqual(questionnaire.seafood_intake_ratio, "5%")
        self.assertEqual(questionnaire.supplement_use, "无营养补充剂")
        self.assertEqual(questionnaire.exercise_frequency, "无规律运动")
        self.assertIn("便秘", questionnaire.symptoms)
        self.assertEqual(questionnaire.bowel_habits, "便秘、腹泻")
        self.assertEqual(questionnaire.msq_system_scores.get("消化道"), 1)
        self.assertEqual(questionnaire.msq_system_scores.get("关节/肌肉"), 1)
        self.assertEqual(questionnaire.msq_system_scores.get("能量/活动"), 1)
        self.assertIn("甲减", questionnaire.goals)

    def test_imports_filled_msq_pdf_text_into_questionnaire(self) -> None:
        service = QuestionnaireImportService()
        pdf_text = """
        功能医学健康问卷 姓名：_ 王堃 性别：_ 男 年龄：_ 31
        第一部分：健康状况与维护 1. 您最近一次体检查出的主要问题？ 桥本氏甲状腺炎，甲减，窦性心律Y
        2. 您是否被诊断出患有某种慢性疾病？ □ 是 Y 否
        第二部分：家族病史 目前正患有何种慢性疾病？ 曾经患有何种慢性疾病？ 曾经接受过何种手术？
        您本人 甲减 无 无 您的父亲 糖尿病、高血压Y 糖尿病、高血压、脑卒中 无 您的母亲 阑尾炎 无 无
        第三部分：生活和饮食习惯 以下可以复选
        1. 您的睡眠质量如何？ □ 很好 □ 多梦 Y早醒 Y 入睡困难 □ 容易入睡，但不深睡 原因:
        2. 您一般什么时间上床睡觉？ 24点 3. 您一般早上什么时间醒来？ 7点半 健康信息调查问卷 第一页
        第三部分：生活和饮食习惯（续）
        4. 您日常三餐主要食用？ Y 以米饭为主食 占比10% 以面食为主食 占比10% Y 猪肉、牛肉、羊肉等（红肉）占比30% Y 鱼类和海鲜 占比5%
        5. 您能够规律地吃早餐吗？ □ 我通常不吃早餐 Y 我有时候不吃早饭，或直接和午餐一起吃 □ 我每天在家吃早餐 Y我通常在外面买早餐吃
        6.常饮用： Y茶 每日 2 杯 Y 咖啡 每日 2 杯
        7. 您喝酒吗？Y 是 □ 否 年数：8 年 8. 您平时饮酒常饮用哪种酒？ 白酒 9. 您每次喝酒的习惯？ □ 少于半杯 □半杯到一杯 □ 二杯到三杯 Y 三杯到四杯 10. 每周外出就餐次数？5次
        11. 经常使用快餐或加工食物？ □ 是Y 否 12. 对食物添加物及防腐剂敏感？ □ 是 Y否
        第四部分：营养品补充 1.您有补充营养食品吗？Y 是 □ 否 如有，请具体说明品种和频率
        □ 鱼油 一日一次 □ 维生素A 一日一次 □ 镁 一日一次 □ 维生素D 一日一次 □ 复合多种维生素B 一日一次
        第五部分：工作 1. 您在工作日每天工作小时数？ ＿10＿ 小时/日 2. 您每天使用电脑时间多长？ 1小时/日
        第六部分：环境因素 5. 长途旅行后会有时差症状？Y 是 □ 否
        第七部分：运动状况 1. 有运动习惯？Y 是 □ 否 6. 目前的体能状况限制了体能活动 Y 是 □ 否
        第八部分：男性/女性状况
        第九部分：症状评估
        级别 序号 症状描述 从来没有 偶尔 轻微 中等 严重 0 1 2 3 4
        2 犹豫不决、难下决定 □ □ Y □ □ 头/脑力方面 3 记忆力变差 □ Y □ □ □
        2 腹胀/胀气 □ Y □ □ □ 消化功能 5 便秘 □ Y □ □ □ 6 腹泻 □ Y □ □ □
        6 头发/皮肤 痤疮 (青春痘) □ □ Y □ □ 1 容易疲劳虚弱，没精神 □ □ Y □ □
        3 全身肌肉无力、肌肉酸痛 □ Y □ □ □ 4 游走性非发炎之关节痛 □ Y □ □ □ 5 睡眠障碍（失眠或嗜睡） □ Y □ □ □
        """
        service._extract_pdf_text = lambda content: pdf_text  # type: ignore[method-assign]

        questionnaire = service.parse(filename="MSQ--test.pdf", content_type="application/pdf", content=b"%PDF")

        self.assertEqual(questionnaire.age, 31)
        self.assertEqual(questionnaire.sex, "male")
        self.assertIn("桥本氏甲状腺炎", questionnaire.known_conditions)
        self.assertIn("父亲：糖尿病、高血压、脑卒中", questionnaire.family_history)
        self.assertEqual(questionnaire.sleep_hours, 7.5)
        self.assertIn("早醒", questionnaire.sleep_quality or "")
        self.assertEqual(questionnaire.red_meat_intake_ratio, "30%")
        self.assertEqual(questionnaire.seafood_intake_ratio, "5%")
        self.assertEqual(questionnaire.dining_out_frequency, "每周5次")
        self.assertIn("鱼油（一日一次）", questionnaire.supplement_use or "")
        self.assertEqual(questionnaire.exercise_frequency, "有运动习惯")
        self.assertIn("便秘", questionnaire.symptoms)
        self.assertEqual(questionnaire.bowel_habits, "便秘、腹泻")
        self.assertEqual(questionnaire.msq_system_scores.get("头部"), 2)
        self.assertEqual(questionnaire.msq_system_scores.get("消化道"), 1)
        self.assertEqual(questionnaire.msq_system_scores.get("皮肤"), 2)


if __name__ == "__main__":
    unittest.main()
