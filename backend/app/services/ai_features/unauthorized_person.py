from .registry import ai_rule


@ai_rule("unauthorized_person", "孔口无关人员")
def unauthorized_person(service, frame):
    if frame is None:
        return False, None

    if service.is_debug_force("unauthorized_person"):
        return service._check_cooldown_and_alarm(
            "围栏入侵管理类",
            "DEBUG: 强制触发围栏入侵报警（链路测试）",
            1.0,
            service._debug_box(frame),
        )

    if service.model is None and not service._load_model_safe():
        return False, None

    try:
        results = service.model(frame, conf=0.45, verbose=False)[0]

        holes = []
        persons = []

        for box in results.boxes:
            cls_id = int(box.cls[0])
            label = service._label_of(results, cls_id)
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            if label in {"hole", "opening", "hole_danger"}:
                holes.append((x1, y1, x2, y2))

            if label == "person":
                persons.append((x1, y1, x2, y2))

        for hx1, hy1, hx2, hy2 in holes:
            hx = (hx1 + hx2) / 2
            hy = (hy1 + hy2) / 2

            for px1, py1, px2, py2 in persons:
                px = (px1 + px2) / 2
                py = (py1 + py2) / 2

                if abs(px - hx) < 300 and abs(py - hy) < 300:
                    return service._check_cooldown_and_alarm(
                        "围栏入侵管理类",
                        "孔口附近5m范围出现无关人员",
                        0.9,
                        [hx1, hy1, hx2, hy2],
                    )

        return False, None
    except Exception as e:
        print(f"⚠️ 无关人员检测出错: {e}")
        return False, None