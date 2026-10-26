/*
 * NekoStreamCam —— 直播跟拍相机 v3（UdonSharp）
 *
 * 风格（参考木几萌直播）：固定机位跟拍胸像，不主动绕圈；NPC 转身角度偏差大了才平滑拉回正脸；
 *   多套预设机位（正面 / 左右 3/4 / 半身 / 俯拍）手动切换 + 手动微调；遇墙自动换到另一边，不穿模。
 *
 * 机位 = 以 NPC 头部挂点（NekoNpc_YUI/StreamCamMount）为中心的方位角 bearing（世界角）、距离、高度。
 *   保持：NPC 原地转头/说话时 bearing 不变（画面稳定），NPC 走动时相机平移跟随保持距离。
 *   拉回：相机方向与 NPC 正脸夹角 > maxFaceAngle 持续 reframeDelay 秒 → bearing 平滑转回（正脸 + 预设偏角）。
 *   避墙：目标机位被挡 → 在首选 bearing 两侧按 ±15°、±30°… 扫描，先找最接近的空位（同等偏差优先离当前相机近的一侧）；首选位重新空出来才回去；
 *         全挡则沿视线拉近兜底。所有切换都经过角速度限幅 + 位置阻尼，不会跳。
 *
 * 画面 → RenderTexture → 仅 driver 可见的 HUD（关/小窗/全屏），OBS 抓 VRChat 窗口即可推流。
 * 按键（driver，桌面）：F7 HUD 三档；F8 锁定/解锁自动拉回；F6 立即拉回正脸；F9 传送到隐藏影棚/传回（去摆 VRChat 自带相机拍影棚屏幕）；
 *   主键盘 1–5 切预设；小键盘 4/6 左右偏角 ±5°，8/2 高度 ±0.05，+/- 距离 ±0.1，7/9 FOV ∓2，0 重置微调。
 * SendCustomEvent：_StreamOff/_StreamPip/_StreamFull/_CycleMode/_Preset0.._Preset4/_ReframeNow/_ToggleAutoReframe/
 *   _NudgeLeft/_NudgeRight/_NudgeUp/_NudgeDown/_NudgeIn/_NudgeOut/_ResetNudge
 * 生成：菜单 NEKO → Build Stream Cam（NekoStreamCamBuilder.cs）。
 */
using UdonSharp;
using UnityEngine;
using VRC.SDKBase;

[UdonBehaviourSyncMode(BehaviourSyncMode.None)]
public class NekoStreamCam : UdonSharpBehaviour
{
    private const int PlayerRenderLayersMask = (1 << 9) | (1 << 10) | (1 << 18);
    private const int HudLayerMask = (1 << 5) | (1 << 12) | (1 << 19); // UI / UiMenu / InternalUI

    [Header("依赖")]
    public NekoNpcTelemetry telemetry;
    [Tooltip("操作者 VRChat 显示名。填了就只有这个人看得到 HUD / 相机渲染 / 按键有效；留空则沿用 Telemetry 的 driver 判定（握手前对所有人开放）")]
    public string operatorDisplayName = "";
    [Tooltip("聊天面板打开或输入框获得焦点时，屏蔽跟拍快捷键")]
    public NekoNpcChatInput chatInput;
    [Tooltip("跟拍中心（NPC 头部挂点，父物体朝向 = NPC 正脸）")]
    public Transform mount;
    public Camera streamCamera;
    public Transform hudQuad;

    [Header("预设机位（索引对应，主键盘 1–5）")]
    [Tooltip("相对 NPC 正脸的偏角（度，0=正面，负=NPC 左侧）")]
    public float[] presetYaw = new float[] { 0f, -30f, 30f, 0f, 0f };
    [Tooltip("距离（米）")]
    public float[] presetDistance = new float[] { 2.6f, 2.6f, 2.6f, 3.2f, 2.6f };
    [Tooltip("相机高出挂点（米）")]
    public float[] presetHeight = new float[] { 0.6f, 0.6f, 0.6f, 0.9f, 1.6f };
    [Tooltip("视场角（度）")]
    public float[] presetFov = new float[] { 30f, 30f, 30f, 34f, 34f };
    [Tooltip("看向挂点上方多少米（负=看得更低，画面里人偏上）")]
    public float[] presetLookUp = new float[] { -0.32f, -0.32f, -0.32f, -0.55f, -0.5f };
    public int startPreset = 0;

    [Header("保持 / 拉回")]
    [Tooltip("相机方向与正脸夹角超过多少度才拉回")]
    public float maxFaceAngle = 45f;
    [Tooltip("超过夹角持续多久才拉回（秒），避免 NPC 一转头就动镜头")]
    public float reframeDelay = 1.5f;
    [Tooltip("拉回后夹角小于多少度视为到位")]
    public float settleAngle = 8f;
    [Tooltip("方位角最大转速（度/秒）")]
    public float bearingSpeed = 25f;
    [Tooltip("方位角加速度（度/秒²）")]
    public float bearingAccel = 20f;
    public bool autoReframe = true;

    [Header("平滑")]
    [Tooltip("相机位置阻尼时间（秒）")]
    public float positionDamping = 0.35f;
    [Tooltip("朝向阻尼（越大越跟手）")]
    public float rotationSmoothing = 10f;
    [Tooltip("中心跟随（越大越跟手）")]
    public float centerSmoothing = 5f;
    [Tooltip("距离/高度变化的阻尼时间（秒）")]
    public float zoomDamping = 0.6f;

    [Header("避墙")]
    public LayerMask environmentMask = (1 << 0) | (1 << 11);
    public float probeRadius = 0.22f;
    public float wallMargin = 0.2f;
    [Tooltip("可用距离比例低于此值视为被挡，开始找别的机位")]
    public float blockedThreshold = 0.75f;
    [Tooltip("候选机位可用比例达到此值才采用")]
    public float clearThreshold = 0.9f;
    [Tooltip("被挡持续多久才换位（秒）")]
    public float avoidDelay = 0.4f;
    [Tooltip("扫描步长（度）")]
    public float scanStepDeg = 15f;
    [Tooltip("最多扫到多少度（两侧）")]
    public float scanMaxDeg = 150f;
    [Tooltip("最近缩到距离的多少（兜底拉近，防止贴脸/穿模）")]
    public float minDistanceFraction = 0.55f;
    public float pullInSpeed = 4f;
    public float pushOutSpeed = 1.0f;

    [Header("隐藏影棚（VRChat 自带相机拍这块屏）")]
    [Tooltip("影棚里的站位（NEKO_StreamStudio/TeleportSpot）；F9 在这里和原地之间来回传送，去摆 VRChat 相机")]
    public Transform studioSpot;
    public KeyCode studioKey = KeyCode.F9;
    [Tooltip("HUD 关闭时也持续渲染（影棚屏幕要用）")]
    public bool renderWithoutHud = true;

    [Header("HUD")]
    public int startMode = 1;
    public Vector3 pipOffset = new Vector3(-0.28f, 0.18f, 0.6f);
    public float pipWidth = 0.26f;
    public float fullDistance = 0.5f;
    public float fullOverscan = 1.06f;
    public float fullAspect = 16f / 9f;
    public bool followHeadRotation = true;

    [Header("按键（桌面模式，仅 driver）")]
    public KeyCode hudKey = KeyCode.F7;
    public KeyCode lockKey = KeyCode.F8;
    public KeyCode reframeKey = KeyCode.F6;
    public KeyCode presetKey0 = KeyCode.Alpha1;
    public KeyCode presetKey1 = KeyCode.Alpha2;
    public KeyCode presetKey2 = KeyCode.Alpha3;
    public KeyCode presetKey3 = KeyCode.Alpha4;
    public KeyCode presetKey4 = KeyCode.Alpha5;
    public KeyCode nudgeLeftKey = KeyCode.Keypad4;
    public KeyCode nudgeRightKey = KeyCode.Keypad6;
    public KeyCode nudgeUpKey = KeyCode.Keypad8;
    public KeyCode nudgeDownKey = KeyCode.Keypad2;
    public KeyCode nudgeInKey = KeyCode.KeypadPlus;
    public KeyCode nudgeOutKey = KeyCode.KeypadMinus;
    public KeyCode fovInKey = KeyCode.Keypad9;
    public KeyCode fovOutKey = KeyCode.Keypad7;
    public KeyCode resetNudgeKey = KeyCode.Keypad0;

    // 运行状态
    private int _mode;
    private int _preset;
    private float _nudgeYaw, _nudgeDist, _nudgeHeight, _nudgeFov;
    private float _bearing;          // 当前方位角（世界，度）
    private float _bearingTarget;    // 目标方位角
    private float _bearingVel;       // 方位角速度
    private float _dist, _distVel;   // 当前距离（阻尼）
    private float _height, _heightVel;
    private float _distFraction = 1f;
    private float _offFaceSince = -1f;
    private float _blockedSince = -1f;
    private bool _reframing;
    private Vector3 _center;
    private Vector3 _camPos, _camVel;
    private Quaternion _camRot;
    private bool _resolved, _init;
    private bool _inStudio;
    private Vector3 _returnPos;
    private Quaternion _returnRot;
    private float _lastAvoidPick = -999f;

    // ---------- 生命周期 ----------
    void Start()
    {
        // 兼容已有场景；正式 Builder 同时写入明确引用。
        if (chatInput == null) chatInput = transform.root.GetComponent<NekoNpcChatInput>();
        _mode = startMode;
        _preset = Mathf.Clamp(startPreset, 0, PresetCount() - 1);
        if (streamCamera != null)
        {
            streamCamera.cullingMask = (streamCamera.cullingMask | PlayerRenderLayersMask) & ~HudLayerMask;
            streamCamera.fieldOfView = CurFov();
        }
        Debug.Log("[NEKO-STREAMCAM] Start v3: mode=" + _mode + " preset=" + _preset + " mount=" + (mount != null) + " cam=" + (streamCamera != null) + " hud=" + (hudQuad != null));
        Apply();
    }

    public override void Interact() { if (!IsDriverLocal()) return; _CycleMode(); }

    // ---------- 公开控制 ----------
    public void _StreamOff() { _mode = 0; Apply(); }
    public void _StreamPip() { _mode = 1; Apply(); }
    public void _StreamFull() { _mode = 2; Apply(); }
    public void _CycleMode() { _mode = (_mode + 1) % 3; Apply(); }
    public void _Preset0() { SetPreset(0); }
    public void _Preset1() { SetPreset(1); }
    public void _Preset2() { SetPreset(2); }
    public void _Preset3() { SetPreset(3); }
    public void _Preset4() { SetPreset(4); }
    public void _ReframeNow() { _bearingTarget = FaceBearing() + CurYaw(); _reframing = true; _offFaceSince = -1f; }
    public void _ToggleAutoReframe() { autoReframe = !autoReframe; }
    public void _ToggleStudio()
    {
        VRCPlayerApi lp = Networking.LocalPlayer;
        if (lp == null || studioSpot == null) return;
        if (!_inStudio)
        {
            _returnPos = lp.GetPosition(); _returnRot = lp.GetRotation();
            lp.TeleportTo(studioSpot.position, studioSpot.rotation);
            _inStudio = true;
        }
        else
        {
            lp.TeleportTo(_returnPos, _returnRot);
            _inStudio = false;
        }
        Debug.Log("[NEKO-STREAMCAM] studio=" + _inStudio);
    }
    public void _NudgeLeft() { _nudgeYaw -= 5f; _ReframeNow(); }
    public void _NudgeRight() { _nudgeYaw += 5f; _ReframeNow(); }
    public void _NudgeUp() { _nudgeHeight += 0.05f; }
    public void _NudgeDown() { _nudgeHeight -= 0.05f; }
    public void _NudgeIn() { _nudgeDist -= 0.1f; }
    public void _NudgeOut() { _nudgeDist += 0.1f; }
    public void _FovIn() { _nudgeFov -= 2f; }
    public void _FovOut() { _nudgeFov += 2f; }
    public void _ResetNudge() { _nudgeYaw = 0f; _nudgeDist = 0f; _nudgeHeight = 0f; _nudgeFov = 0f; _ReframeNow(); }

    private void SetPreset(int i)
    {
        if (i < 0 || i >= PresetCount()) return;
        _preset = i;
        _ReframeNow();
        Debug.Log("[NEKO-STREAMCAM] preset " + i);
    }

    // ---------- 工具 ----------
    private int PresetCount() { return presetYaw == null ? 0 : Mathf.Min(presetYaw.Length, Mathf.Min(presetDistance.Length, Mathf.Min(presetHeight.Length, Mathf.Min(presetFov.Length, presetLookUp.Length)))); }
    private float CurYaw() { return presetYaw[_preset] + _nudgeYaw; }
    private float CurDist() { return Mathf.Max(0.6f, presetDistance[_preset] + _nudgeDist); }
    private float CurHeight() { return presetHeight[_preset] + _nudgeHeight; }
    private float CurFov() { return Mathf.Clamp(presetFov[_preset] + _nudgeFov, 15f, 90f); }
    private float CurLookUp() { return presetLookUp[_preset]; }
    private bool IsDriverLocal()
    {
        if (operatorDisplayName != null && operatorDisplayName.Length > 0)
        {
            VRCPlayerApi lp = Networking.LocalPlayer;
            return lp != null && lp.displayName == operatorDisplayName;
        }
        return telemetry == null || telemetry.IsDriver();
    }

    // NPC 正脸所在方位角（相机应该站在脸前方 = 正脸方向）
    private float FaceBearing()
    {
        Transform t = mount != null && mount.parent != null ? mount.parent : mount;
        Vector3 f = t.forward; f.y = 0f;
        if (f.sqrMagnitude < 0.0001f) return _bearing;
        return Mathf.Atan2(f.z, f.x) * Mathf.Rad2Deg;
    }

    private Vector3 OffsetFor(float bearingDeg, float dist, float height)
    {
        float r = bearingDeg * Mathf.Deg2Rad;
        return new Vector3(Mathf.Cos(r) * dist, height, Mathf.Sin(r) * dist);
    }

    // 中心到某机位的可用比例（1 = 无遮挡）
    private float FreeFraction(float bearingDeg, float dist, float height)
    {
        Vector3 offset = OffsetFor(bearingDeg, dist, height);
        float full = offset.magnitude;
        RaycastHit hit;
        if (Physics.SphereCast(_center, probeRadius, offset / full, out hit, full, environmentMask.value, QueryTriggerInteraction.Ignore))
            return Mathf.Clamp01((hit.distance - wallMargin) / full);
        return 1f;
    }

    private void Apply()
    {
        bool driver = IsDriverLocal();
        bool on = driver && (_mode > 0 || renderWithoutHud);
        if (streamCamera != null) { streamCamera.enabled = on; streamCamera.fieldOfView = CurFov(); }
        if (hudQuad != null)
        {
            hudQuad.gameObject.SetActive(driver && _mode > 0);
            if (_mode == 2)
            {
                float h = 2f * fullDistance * Mathf.Tan(30f * Mathf.Deg2Rad) * fullOverscan;
                hudQuad.localScale = new Vector3(h * fullAspect, h, 1f);
            }
            else hudQuad.localScale = new Vector3(pipWidth, pipWidth * 9f / 16f, 1f);
        }
        Debug.Log("[NEKO-STREAMCAM] mode=" + _mode + " on=" + on);
    }

    private bool IsChatInputActive()
    {
        if (chatInput == null) return false;
        if (chatInput.panelRoot != null && chatInput.panelRoot.gameObject.activeInHierarchy) return true;
        if (chatInput.inputField != null && chatInput.inputField.isFocused) return true;
        // 避免打开聊天的同一帧因脚本执行顺序不同而触发快捷键。
        return Input.GetKeyDown(chatInput.openKey);
    }

    private void HandleKeys()
    {
        if (Input.GetKeyDown(hudKey)) _CycleMode();
        if (Input.GetKeyDown(lockKey)) _ToggleAutoReframe();
        if (Input.GetKeyDown(reframeKey)) _ReframeNow();
        if (Input.GetKeyDown(studioKey)) _ToggleStudio();
        if (Input.GetKeyDown(presetKey0)) SetPreset(0);
        if (Input.GetKeyDown(presetKey1)) SetPreset(1);
        if (Input.GetKeyDown(presetKey2)) SetPreset(2);
        if (Input.GetKeyDown(presetKey3)) SetPreset(3);
        if (Input.GetKeyDown(presetKey4)) SetPreset(4);
        if (Input.GetKeyDown(nudgeLeftKey)) _NudgeLeft();
        if (Input.GetKeyDown(nudgeRightKey)) _NudgeRight();
        if (Input.GetKeyDown(nudgeUpKey)) _NudgeUp();
        if (Input.GetKeyDown(nudgeDownKey)) _NudgeDown();
        if (Input.GetKeyDown(nudgeInKey)) _NudgeIn();
        if (Input.GetKeyDown(nudgeOutKey)) _NudgeOut();
        if (Input.GetKeyDown(fovInKey)) _FovIn();
        if (Input.GetKeyDown(fovOutKey)) _FovOut();
        if (Input.GetKeyDown(resetNudgeKey)) _ResetNudge();
    }

    // ---------- 主循环 ----------
    // 放在 PostLateUpdate：NekoArdyRigLab 在 LateUpdate 里移动/转动 NPC 根节点，
    // 若在 Update 里定位，跟拍中心和 HUD 会晚一帧、并被根节点当帧的位移带着晃。
    public override void PostLateUpdate()
    {
        if (!_resolved)
        {
            if (Networking.LocalPlayer == null) return;
            _resolved = true;
            // HUD 是贴在操作者头前的世界空间物体，不能挂在会被动作系统移动的 NPC 根节点下。
            if (hudQuad != null && hudQuad.parent != null) hudQuad.SetParent(null, true);
            Apply();
        }
        if (!IsDriverLocal()) return;
        // 只屏蔽输入，聊天时仍正常跟拍和刷新 HUD。
        if (!IsChatInputActive()) HandleKeys();
        if (mount == null || streamCamera == null || !streamCamera.enabled) return;

        float dt = Time.deltaTime;
        if (dt <= 0f) return;
        float now = Time.time;

        // 中心跟随
        Vector3 target = mount.position;
        if (!_init) _center = target;
        else _center = Vector3.Lerp(_center, target, 1f - Mathf.Exp(-centerSmoothing * dt));

        // 距离/高度阻尼（预设切换、微调都是缓的）
        float wantDist = CurDist(), wantHeight = CurHeight();
        if (!_init) { _dist = wantDist; _height = wantHeight; }
        else
        {
            _dist = Mathf.SmoothDamp(_dist, wantDist, ref _distVel, zoomDamping, 100f, dt);
            _height = Mathf.SmoothDamp(_height, wantHeight, ref _heightVel, zoomDamping, 100f, dt);
        }

        // 初始化方位角 = 正脸 + 预设偏角
        float face = FaceBearing();
        if (!_init) { _bearing = face + CurYaw(); _bearingTarget = _bearing; }

        // 1) 角度偏差大了 → 拉回正脸（带迟滞）
        float preferred = face + CurYaw();
        float offFace = Mathf.Abs(Mathf.DeltaAngle(_bearing, preferred));
        bool preferredClear = FreeFraction(preferred, _dist, _height) >= clearThreshold;
        if (!preferredClear) { _reframing = false; _offFaceSince = -1f; }
        if (autoReframe && preferredClear)
        {
            if (offFace > maxFaceAngle)
            {
                if (_offFaceSince < 0f) _offFaceSince = now;
                else if (now - _offFaceSince >= reframeDelay) { _reframing = true; _bearingTarget = preferred; }
            }
            else _offFaceSince = -1f;
        }
        if (_reframing)
        {
            // 拉回过程中目标持续跟着正脸走，直到到位
            _bearingTarget = preferred;
            if (offFace <= settleAngle) _reframing = false;
        }

        // 2) 避墙：目标机位被挡 → 两侧扫描找最近的空位（同等偏差时优先“另一边”）
        float freeAtTarget = FreeFraction(_bearingTarget, _dist, _height);
        if (freeAtTarget < blockedThreshold)
        {
            if (_blockedSince < 0f) _blockedSince = now;
            if (now - _blockedSince >= avoidDelay)
            {
                float best = _bearingTarget; bool found = false;
                // 首选 = 正脸+偏角；先试镜像（另一边），再逐步扩大
                float mirror = face - CurYaw();
                if (Mathf.Abs(Mathf.DeltaAngle(mirror, _bearingTarget)) > 1f && FreeFraction(mirror, _dist, _height) >= clearThreshold) { best = mirror; found = true; }
                for (float d = scanStepDeg; !found && d <= scanMaxDeg; d += scanStepDeg)
                {
                    // 同等偏差时先看离当前相机更近的一侧（少甩镜头，不会两边来回跳）
                    float sideA = preferred + d, sideB = preferred - d;
                    float a1 = Mathf.Abs(Mathf.DeltaAngle(sideA, _bearing)), b1 = Mathf.Abs(Mathf.DeltaAngle(sideB, _bearing));
                    float first = a1 <= b1 ? sideA : sideB, second = a1 <= b1 ? sideB : sideA;
                    if (FreeFraction(first, _dist, _height) >= clearThreshold) { best = first; found = true; break; }
                    if (FreeFraction(second, _dist, _height) >= clearThreshold) { best = second; found = true; break; }
                }
                if (found) { _bearingTarget = best; _blockedSince = -1f; _lastAvoidPick = best; }
            }
        }
        else _blockedSince = -1f;
        // 首选位重新空出来（例如 NPC 转回来、离开墙）→ 回到首选位
        if (preferredClear && Mathf.Abs(Mathf.DeltaAngle(_bearingTarget, preferred)) > 1f && !_reframing && autoReframe && _lastAvoidPick > -900f)
        {
            _bearingTarget = preferred; _lastAvoidPick = -999f;
        }

        // 3) 方位角向目标平滑转动（限速 + 加速度）
        float delta = Mathf.DeltaAngle(_bearing, _bearingTarget);
        float wantVel = Mathf.Clamp(delta * 2.5f, -bearingSpeed, bearingSpeed);
        if (Mathf.Abs(delta) < 0.05f) wantVel = 0f;
        _bearingVel = Mathf.MoveTowards(_bearingVel, wantVel, bearingAccel * dt);
        _bearing += _bearingVel * dt;
        if (_bearing > 360f) _bearing -= 360f; else if (_bearing < -360f) _bearing += 360f;

        // 4) 当前机位兜底拉近（永不穿墙）
        Vector3 offset = OffsetFor(_bearing, _dist, _height);
        float fullDist = offset.magnitude;
        float allowed = Mathf.Max(FreeFraction(_bearing, _dist, _height), minDistanceFraction);
        if (allowed < _distFraction) _distFraction = Mathf.Max(allowed, _distFraction - pullInSpeed * dt / fullDist);
        else _distFraction = Mathf.Min(allowed, _distFraction + pushOutSpeed * dt / fullDist);

        Vector3 desired = _center + offset * _distFraction;
        Vector3 lookAt = _center + Vector3.up * CurLookUp();

        if (!_init) { _camPos = desired; _camVel = Vector3.zero; _camRot = Quaternion.LookRotation(lookAt - _camPos, Vector3.up); _init = true; }
        else
        {
            _camPos = Vector3.SmoothDamp(_camPos, desired, ref _camVel, positionDamping, 100f, dt);
            // 朝向永远从相机“实际”位置看向脸：位置在过渡中也不会把人甩到画面边上
            Quaternion desiredRot = Quaternion.LookRotation(lookAt - _camPos, Vector3.up);
            _camRot = Quaternion.Slerp(_camRot, desiredRot, 1f - Mathf.Exp(-rotationSmoothing * dt));
        }
        Transform ct = streamCamera.transform;
        ct.position = _camPos;
        ct.rotation = _camRot;
        float fov = CurFov();
        if (Mathf.Abs(streamCamera.fieldOfView - fov) > 0.01f) streamCamera.fieldOfView = Mathf.Lerp(streamCamera.fieldOfView, fov, 1f - Mathf.Exp(-4f * dt));

        // HUD 贴到 driver 头前
        if (hudQuad == null || _mode == 0) return;
        VRCPlayerApi local = Networking.LocalPlayer;
        if (local == null) return;
        VRCPlayerApi.TrackingData head = local.GetTrackingData(VRCPlayerApi.TrackingDataType.Head);
        Quaternion rot = head.rotation;
        if (!followHeadRotation && _mode != 2)
        {
            Vector3 f = rot * Vector3.forward; f.y = 0f;
            if (f.sqrMagnitude > 0.0001f) rot = Quaternion.LookRotation(f.normalized, Vector3.up);
        }
        Vector3 hudOff = _mode == 2 ? new Vector3(0f, 0f, fullDistance) : pipOffset;
        hudQuad.position = head.position + rot * hudOff;
        hudQuad.rotation = rot;
    }
}
