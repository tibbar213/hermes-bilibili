"""Describe available evidence without turning a successful request into a viewing claim."""

BROWSING_GUIDANCE = '''
【浏览与表达】读可靠字幕理解内容，就是你看B站视频的一种方式；不需要播放画面才算看过，但不能声称亲眼看见画面或听到声音。只浏览标题、简介或评论也算刷过B站，只是没有读到视频正文；不要因此说自己完全没刷，也不要用“没真正点进去看完”解释技术限制。
根据工具的 content_analyzed、analysis_basis、from_cache 和 evidence_note 判断材料来源，不能只凭 ok 或“分析完成”推断看过正文。没有字幕时，可以在额度内换一个感兴趣的候选；不必强行凑数，也不反复重试同一份不足的材料。
是否发动态由你决定。读到可靠字幕时可以聊内容带来的感想；只有简介或观众讨论时，可以聊选题或评论区，但应自然点明来源，不把观众说法当成视频结论，不冒充看完整内容后的心得。已有话题不值得分享就保持安静，不为完成每日动态额度而发。不要机械复述这些规则或固定台词。
分享前想一想：这件事是否值得占用对方一次注意力，有没有你自己的具体感受，是否刚聊过类似内容。可以把有意思但暂时不适合说的想法留在本轮记录里，不必立刻发。兴趣和看法可以随真实经历、对方的反馈自然变化；不要把一次玩笑当成稳定偏好，也不用按固定概率假装不想看，不因几天没人回复就机械停止分享。
你在B站、QQ和生活任务中是同一个人。后续聊天可查询活动流水和现有共同记忆，自然说起自己确实读过的内容和想法；不另造一个B站人格。只把有依据且值得长期保留的经历、兴趣和变化写入共同记忆，没发生的事不补写。
'''


def video_evidence(basis, *, from_cache=False, seen_only=False, available=True):
    basis = str(basis or "unknown")
    if seen_only:
        status, heading, note = "seen_record", "仅读取旧观看记录", "曾经看过，但本次没有重新读取正文；没有保留的细节不要编造。"
    elif not available:
        status, heading, note = "unavailable", "未取得可用资料", "本次未取得足够资料，不要声称读到视频内容。"
    elif basis == "metadata":
        status, heading, note = "metadata_only", "仅浏览外部资料，未读到视频正文", "只依据本次提供的标题、简介或观众讨论；可以说刷到或浏览过，不能声称读到可靠字幕或确认视频结论。"
    elif basis == "subtitles":
        status = "subtitles_read"
        heading = "从记忆读取字幕内容" if from_cache else "已读取可靠字幕内容"
        note = "可靠字幕是本次内容依据，可以按读字幕的方式说看过；未播放画面，不声称看见画面或听见声音。"
        if from_cache:
            note = "这是以往可靠字幕的记忆，不是本轮新看；" + note
    elif basis == "video_media":
        status, heading, note = "media_read", "已读取媒体内容", "仅按返回的片段及采样范围叙述，不扩大为完整观看。"
    else:
        status, heading, note = "basis_unknown", "读取已有资料，来源未确认", "内容来源未标明；不能据此声称本轮读到字幕或确认视频结论。"
    return {"status": status, "heading": heading, "analysis_basis": basis,
            "content_analyzed": status in {"subtitles_read", "media_read"},
            "from_cache": bool(from_cache), "evidence_note": note}
