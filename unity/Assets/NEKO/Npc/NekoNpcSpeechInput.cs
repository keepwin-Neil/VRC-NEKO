// Explicit remote-player speaking leases. Audio is captured only by the
// driver's separate Windows process-loopback worker, never by this Udon code.
using UdonSharp;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;
using VRC.SDK3.UdonNetworkCalling;
using VRC.Udon.Common.Interfaces;
using TMPro;

[UdonBehaviourSyncMode(BehaviourSyncMode.Manual)]
public class NekoNpcSpeechInput : UdonSharpBehaviour
{
    public NekoNpcTelemetry telemetry;
    public NekoMidiRouter router;
    public NekoNpcPerception perception;
    public NekoNpcSync authoritySync;
    public NekoNpcVoice voice;
    public Transform npcRoot;
    public AudioSource[] sceneAudioSources;
    public TextMeshProUGUI statusText;
    public TextMeshProUGUI settingsToggleText;
    public GameObject settingsBody;
    public Button startButton;
    public Button finishButton;
    public Button cancelButton;
    public float interactionRange = 4f;
    public float leaseSeconds = 12f;
    public float nextStartDelaySeconds = 5f;

    [UdonSynced] private int _speakerPid = -1;
    [UdonSynced] private int _requestSeq;
    [UdonSynced] private int _leaseSession;
    [UdonSynced] private double _leaseUntil;
    [UdonSynced] private double _nextStartAt;
    private int _speakerSlot = -1;
    private int _lastOwnerSession;
    private int[] _lastPid = new int[64];
    private int[] _lastSeq = new int[64];
    private int _nextLocalSeq = 1;
    private int _localRequestSeq;
    private int _localRequestSession;
    private bool _awaitingStart;
    private bool _awaitingClose;
    private float _pendingUntil;
    private float _noticeUntil;
    private string _notice = "";
    private bool _settingsVisible = true;
    private bool _hadAuthority;
    private float _nextOwnershipAttempt;
    private bool _isolating;
    private VRCPlayerApi[] _players = new VRCPlayerApi[128];
    private VRCPlayerApi[] _savedPlayers = new VRCPlayerApi[128];
    private float[] _savedNear = new float[128];
    private float[] _savedFar = new float[128];
    private bool[] _savedLowpass = new bool[128];
    private bool[] _savedMute;

    void Start()
    {
        interactionRange = Mathf.Clamp(interactionRange, .5f, 4f);
        leaseSeconds = Mathf.Clamp(leaseSeconds, 2f, 12f);
        nextStartDelaySeconds = Mathf.Max(5f, nextStartDelaySeconds);
        UpdatePanel();
    }

    private bool HasDriverIdentity()
    {
        return telemetry != null && router != null && telemetry.GetSession() > 0
            && telemetry.IsDriver() && router.HasLocalDriverAuthority();
    }

    private bool HasAuthority() { return HasDriverIdentity() && Networking.IsOwner(gameObject); }

    private int VisibleSession()
    {
        return authoritySync != null && authoritySync.GetDriverPid() > 0 ? authoritySync.GetSession() : 0;
    }

    private bool IsNearby(VRCPlayerApi player)
    {
        return player != null && player.IsValid() && npcRoot != null
            && Vector3.Distance(player.GetPosition(), npcRoot.position) <= interactionRange;
    }

    public void _StartSpeaking()
    {
        VRCPlayerApi local = Networking.LocalPlayer;
        int session = VisibleSession();
        if (local == null || session <= 0)
        { Notice("YUI 尚未连接，请等待控制者启动后端。"); return; }
        if (router != null && local.playerId == router.GetClaimedDriverPid())
        { Notice("控制者本人的麦克风不会进入远端回环，请使用文字聊天。"); return; }
        if (!IsNearby(local)) { Notice("请先靠近 YUI，距离需在 4 米以内。"); return; }
        double now = Networking.GetServerTimeInSeconds();
        if (_speakerPid > 0 && now < _leaseUntil) { Notice("已有玩家正在说话，请稍候。"); return; }
        if (now < _nextStartAt) { Notice("上一轮正在结束，请等待几秒。"); return; }
        if (_awaitingStart || _awaitingClose) return;
        _localRequestSeq = _nextLocalSeq;
        _nextLocalSeq = _nextLocalSeq >= 2147483646 ? 1 : _nextLocalSeq + 1;
        _localRequestSession = session;
        _awaitingStart = true; _pendingUntil = Time.realtimeSinceStartup + 4f;
        SendCustomNetworkEvent(NetworkEventTarget.Owner, nameof(ReceiveSpeechRequest),
            _localRequestSession, _localRequestSeq, 0);
        UpdatePanel();
    }

    public void _FinishSpeaking() { SendCloseRequest(1); }
    public void _CancelSpeaking() { SendCloseRequest(2); }

    private void SendCloseRequest(int action)
    {
        VRCPlayerApi local = Networking.LocalPlayer;
        if (local == null || _awaitingClose) return;
        if (!_awaitingStart && (_speakerPid != local.playerId || _requestSeq != _localRequestSeq)) return;
        _awaitingStart = false; _awaitingClose = true;
        _pendingUntil = Time.realtimeSinceStartup + 4f;
        SendCustomNetworkEvent(NetworkEventTarget.Owner, nameof(ReceiveSpeechRequest),
            _localRequestSession, _localRequestSeq, action);
        Notice(action == 1 ? "已请求结束；后台将识别并回复。" : "已请求取消本轮语音。");
    }

    public void _ToggleSettings()
    {
        if (_settingsVisible) _CancelSpeaking();
        _settingsVisible = !_settingsVisible;
        if (settingsBody != null) settingsBody.SetActive(_settingsVisible);
        if (settingsToggleText != null) settingsToggleText.text = _settingsVisible ? "收起" : "打开语音";
    }

    [NetworkCallable(maxEventsPerSecond: 4)]
    public void ReceiveSpeechRequest(int session, int requestSequence, int action)
    {
        if (!NetworkCalling.InNetworkCall || !HasAuthority() || perception == null) return;
        if (session != telemetry.GetSession() || session <= 0 || requestSequence <= 0 || action < 0 || action > 2) return;
        VRCPlayerApi sender = NetworkCalling.CallingPlayer;
        if (sender == null || !sender.IsValid() || sender.isLocal || sender.playerId == router.GetClaimedDriverPid()) return;
        int slot = perception.SlotOf(sender);
        if (slot < 0) { perception.RebuildSlots(); slot = perception.SlotOf(sender); }
        if (slot < 0 || slot >= 64) return;
        if (_lastOwnerSession != session)
        {
            _lastOwnerSession = session;
            for (int i = 0; i < 64; i++) { _lastPid[i] = -1; _lastSeq[i] = 0; }
        }
        if (_lastPid[slot] != sender.playerId) { _lastPid[slot] = sender.playerId; _lastSeq[slot] = 0; }
        double now = Networking.GetServerTimeInSeconds();
        if (action == 0)
        {
            // Same active request is an idempotent retransmission, not a new lease.
            if (_speakerPid == sender.playerId && _requestSeq == requestSequence && _leaseSession == session) return;
            if (requestSequence <= _lastSeq[slot] || _speakerPid > 0 || now < _nextStartAt || !IsNearby(sender)) return;
            _lastSeq[slot] = requestSequence;
            _speakerPid = sender.playerId; _speakerSlot = slot; _requestSeq = requestSequence;
            _leaseSession = session; _leaseUntil = now + leaseSeconds;
            _hadAuthority = true;
            BeginIsolation();
            EmitState("start");
            RequestSerialization();
        }
        else
        {
            if (_speakerPid != sender.playerId || _requestSeq != requestSequence || _leaseSession != session) return;
            EndOwnerLease(action == 1 && IsNearby(sender) && now < _leaseUntil ? "finish" : "cancel");
        }
        UpdatePanel();
    }

    private void EmitState(string state)
    {
        if (!HasAuthority() || _leaseSession != telemetry.GetSession() || _speakerPid <= 0 || _speakerSlot < 0) return;
        telemetry.EmitForced("player.speech_input", "\"slot\":" + _speakerSlot + ",\"pid\":" + _speakerPid
            + ",\"request_seq\":" + _requestSeq + ",\"state\":\"" + state + "\"");
    }

    private void EndOwnerLease(string state)
    {
        // Restore local audio independently of telemetry or networking availability.
        RestoreIsolation();
        if (_speakerPid <= 0) return;
        if (HasAuthority() && _leaseSession == telemetry.GetSession()) EmitState(state);
        _speakerPid = -1; _speakerSlot = -1; _leaseUntil = 0;
        _nextStartAt = Networking.GetServerTimeInSeconds() + nextStartDelaySeconds;
        if (HasAuthority()) RequestSerialization();
    }

    void Update()
    {
        // The lease is Manual sync on its own child, independent of the NPC
        // root's Continuous sync. Only the actual claimed driver may acquire it.
        if (Time.realtimeSinceStartup >= _nextOwnershipAttempt && HasDriverIdentity() && !Networking.IsOwner(gameObject))
        {
            _nextOwnershipAttempt = Time.realtimeSinceStartup + 1f;
            VRCPlayerApi driver = Networking.LocalPlayer;
            if (driver != null && driver.IsValid()) Networking.SetOwner(driver, gameObject);
        }
        bool authority = HasAuthority();
        if (!authority && _isolating) RestoreIsolation();
        if (authority && !_hadAuthority && _speakerPid > 0) EndOwnerLease("cancel");
        _hadAuthority = authority;
        if (authority && _speakerPid > 0)
        {
            VRCPlayerApi speaker = VRCPlayerApi.GetPlayerById(_speakerPid);
            if (_leaseSession != telemetry.GetSession() || Networking.GetServerTimeInSeconds() >= _leaseUntil || !IsNearby(speaker))
                EndOwnerLease("cancel");
        }
        VRCPlayerApi local = Networking.LocalPlayer;
        if (_awaitingStart && local != null && _speakerPid == local.playerId && _requestSeq == _localRequestSeq) _awaitingStart = false;
        if (_awaitingClose && (_speakerPid < 0 || _requestSeq != _localRequestSeq)) _awaitingClose = false;
        if ((_awaitingStart || _awaitingClose) && Time.realtimeSinceStartup >= _pendingUntil)
        { _awaitingStart = false; _awaitingClose = false; Notice("未收到发言确认，请确认 YUI 后端在线后重试。"); }
        UpdatePanel();
    }

    void LateUpdate()
    {
        if (!_isolating) return;
        if (!HasAuthority()) { RestoreIsolation(); return; }
        VRCPlayerApi.GetPlayers(_players);
        for (int i = 0; i < _players.Length; i++)
        {
            VRCPlayerApi player = _players[i];
            if (player == null || !player.IsValid()) continue;
            RememberPlayer(player);
            ApplyPlayerIsolation(player);
        }
        if (sceneAudioSources != null)
            for (int i = 0; i < sceneAudioSources.Length; i++) if (sceneAudioSources[i] != null) sceneAudioSources[i].mute = true;
    }

    private void BeginIsolation()
    {
        RestoreIsolation();
        for (int i = 0; i < _savedPlayers.Length; i++) _savedPlayers[i] = null;
        int count = sceneAudioSources == null ? 0 : sceneAudioSources.Length;
        _savedMute = new bool[count];
        for (int i = 0; i < count; i++) if (sceneAudioSources[i] != null)
        { _savedMute[i] = sceneAudioSources[i].mute; sceneAudioSources[i].mute = true; }
        _isolating = true;
        VRCPlayerApi.GetPlayers(_players);
        for (int i = 0; i < _players.Length; i++)
        {
            VRCPlayerApi player = _players[i];
            if (player == null || !player.IsValid()) continue;
            RememberPlayer(player); ApplyPlayerIsolation(player);
        }
    }

    private void RememberPlayer(VRCPlayerApi player)
    {
        int free = -1;
        for (int i = 0; i < _savedPlayers.Length; i++)
        {
            VRCPlayerApi saved = _savedPlayers[i];
            if (saved != null && saved.IsValid() && saved.playerId == player.playerId) return;
            if (free < 0 && (saved == null || !saved.IsValid())) free = i;
        }
        if (free < 0) return;
        _savedPlayers[free] = player;
        _savedNear[free] = player.GetVoiceDistanceNear();
        _savedFar[free] = player.GetVoiceDistanceFar();
        _savedLowpass[free] = player.GetVoiceLowpass();
    }

    private void ApplyPlayerIsolation(VRCPlayerApi player)
    {
        if (player.playerId == _speakerPid)
        { player.SetVoiceDistanceFar(1000000f); player.SetVoiceDistanceNear(1000f); player.SetVoiceLowpass(false); }
        else { player.SetVoiceDistanceNear(0f); player.SetVoiceDistanceFar(0f); }
        // Avatar-audio settings have no getters in this SDK. Do not guess or
        // overwrite their prior values; the panel explains this limitation.
    }

    private void RestoreIsolation()
    {
        if (!_isolating) return;
        _isolating = false;
        for (int i = 0; i < _savedPlayers.Length; i++)
        {
            VRCPlayerApi player = _savedPlayers[i];
            if (player != null && player.IsValid())
            { player.SetVoiceDistanceFar(_savedFar[i]); player.SetVoiceDistanceNear(_savedNear[i]); player.SetVoiceLowpass(_savedLowpass[i]); }
            _savedPlayers[i] = null;
        }
        if (sceneAudioSources != null && _savedMute != null)
            for (int i = 0; i < sceneAudioSources.Length && i < _savedMute.Length; i++)
                if (sceneAudioSources[i] != null) sceneAudioSources[i].mute = _savedMute[i];
        // A global mute change during capture must survive restoration.
        if (voice != null && voice.voiceSource != null) voice.voiceSource.mute = voice.IsGloballyMuted();
    }

    public override void OnPlayerJoined(VRCPlayerApi player)
    { if (_isolating && player != null && player.IsValid()) { RememberPlayer(player); ApplyPlayerIsolation(player); } }

    public override void OnPlayerLeft(VRCPlayerApi player)
    { if (player != null && player.playerId == _speakerPid && HasAuthority()) EndOwnerLease("cancel"); }

    public override void OnOwnershipTransferred(VRCPlayerApi player)
    { if (!HasAuthority()) RestoreIsolation(); }

    public override bool OnOwnershipRequest(VRCPlayerApi requestingPlayer, VRCPlayerApi requestedOwner)
    {
        return router != null && requestedOwner != null && requestedOwner.IsValid()
            && requestedOwner.playerId > 0 && requestedOwner.playerId == router.GetClaimedDriverPid();
    }

    public override void OnDeserialization() { UpdatePanel(); }

    void OnDisable()
    {
        if (HasAuthority() && _speakerPid > 0) EndOwnerLease("cancel");
        RestoreIsolation();
    }

    private void Notice(string text)
    { _notice = text; _noticeUntil = Time.realtimeSinceStartup + 4f; UpdatePanel(); }

    private void UpdatePanel()
    {
        VRCPlayerApi local = Networking.LocalPlayer;
        double now = Networking.GetServerTimeInSeconds();
        int session = VisibleSession();
        bool connected = session > 0;
        bool active = connected && _speakerPid > 0 && _leaseSession == session && now < _leaseUntil;
        bool mine = active && local != null && _speakerPid == local.playerId && _requestSeq == _localRequestSeq;
        bool isDriver = local != null && router != null && local.playerId == router.GetClaimedDriverPid();
        if (startButton != null) startButton.interactable = connected && !isDriver && !active && !_awaitingStart && !_awaitingClose && now >= _nextStartAt;
        if (finishButton != null) finishButton.interactable = mine && !_awaitingClose;
        if (cancelButton != null) cancelButton.interactable = (mine || _awaitingStart) && !_awaitingClose;
        if (statusText == null) return;
        if (Time.realtimeSinceStartup < _noticeUntil) { statusText.text = _notice; return; }
        if (!connected) { statusText.text = "等待 YUI 后端连接。"; return; }
        if (_awaitingStart) { statusText.text = "正在申请发言，请先解除 VRChat 静音。"; return; }
        if (_awaitingClose) { statusText.text = "正在结束本轮语音……"; return; }
        if (active)
        {
            VRCPlayerApi player = VRCPlayerApi.GetPlayerById(_speakerPid);
            string name = player == null ? "其他玩家" : player.displayName;
            int remaining = Mathf.Max(0, Mathf.CeilToInt((float)(_leaseUntil - now)));
            if (mine) statusText.text = remaining > leaseSeconds - 1f ? "准备收音，请稍候再说……" : "请说话，剩余 " + remaining + " 秒；说完点结束并发送。";
            else statusText.text = name + " 正在说话（剩余 " + remaining + " 秒）。";
            return;
        }
        if (now < _nextStartAt) { statusText.text = "上一轮结束中，请等待 " + Mathf.CeilToInt((float)(_nextStartAt - now)) + " 秒。"; return; }
        statusText.text = isDriver ? "此入口供其他玩家使用；控制者本人请使用文字聊天。" : "先解除 VRChat 静音，再点击开始说话。";
    }
}
