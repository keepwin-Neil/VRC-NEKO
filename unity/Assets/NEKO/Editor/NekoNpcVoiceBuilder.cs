#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using TMPro;
using UdonSharp;
using UdonSharpEditor;
using UnityEditor;
using UnityEditor.Events;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;
using VRC.SDKBase;
using VRC.SDK3.Components;
using VRC.SDK3.Video.Components.AVPro;
using VRC.Udon;

public static class NekoNpcVoiceBuilder
{
    const string PlayerPrefab = "Packages/com.vrchat.worlds/Samples/UdonExampleScene/Prefabs/VideoPlayers/UdonSyncPlayer (AVPro).prefab";
    const string DefaultUrl = "";

    [MenuItem("NEKO/Voice/Configure YUI Spatial Voice")]
    public static void Configure() { ConfigureWithUrl(DefaultUrl); }

    public static void EnsureVoiceProgram()
    {
        const string path = "Assets/NEKO/Npc/NekoNpcVoice.asset";
        var program = AssetDatabase.LoadAssetAtPath<UdonSharpProgramAsset>(path);
        if (program == null)
        {
            program = ScriptableObject.CreateInstance<UdonSharpProgramAsset>();
            program.sourceCsScript = AssetDatabase.LoadAssetAtPath<MonoScript>("Assets/NEKO/Npc/NekoNpcVoice.cs");
            if (program.sourceCsScript == null) throw new InvalidOperationException("Import the voice C# script first.");
            AssetDatabase.CreateAsset(program, path); AssetDatabase.SaveAssets();
            // SDK 3.10.4 caches the program list before this new asset exists.
            typeof(UdonSharpProgramAsset).GetMethod("ClearProgramAssetCache", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        }
        if (program.ScriptVersion < UdonSharpProgramVersion.CurrentVersion)
        {
            program.ScriptVersion = UdonSharpProgramVersion.CurrentVersion;
            EditorUtility.SetDirty(program); AssetDatabase.SaveAssets();
        }
    }

    public static void ConfigureWithUrl(string defaultUrl)
    {
        EnsureVoiceProgram();
        var scene = EditorSceneManager.GetActiveScene();
        if ((scene.path != "Assets/SAVE.unity" && scene.path != "Assets/SAVE_YUI_VoiceTest.unity") || EditorApplication.isPlaying)
            throw new InvalidOperationException("Open SAVE or SAVE_YUI_VoiceTest in edit mode first.");
        var router = UnityEngine.Object.FindObjectOfType<NekoMidiRouter>();
        if (router == null || router.locomotion == null || router.locomotion.npcRoot == null)
            throw new InvalidOperationException("YUI router/root is missing.");
        if (!(defaultUrl == "" || ((defaultUrl.StartsWith("https://") || defaultUrl.StartsWith("http://127.0.0.1:17864/")) && defaultUrl.EndsWith(".m3u8"))))
            throw new ArgumentException("Expected an HTTPS stream or the local test URL.");
        var root = router.locomotion.npcRoot;
        var voice = root.GetComponentInChildren<NekoNpcVoice>(true);
        if (voice == null)
        {
            Transform head = null;
            foreach (var animator in root.GetComponentsInChildren<Animator>(true))
                if (animator.isHuman && animator.avatar != null) { head = animator.GetBoneTransform(HumanBodyBones.Head); if (head != null) break; }
            if (head == null) head = root.Find("EyeAnchor");
            if (head == null) throw new InvalidOperationException("YUI has no head or EyeAnchor.");
            var speaker = new GameObject("YuiSpatialVoice");
            Undo.RegisterCreatedObjectUndo(speaker, "Configure YUI voice");
            speaker.transform.SetParent(head, false);
            speaker.transform.localPosition = Vector3.zero;
            voice = UdonSharpUndo.AddComponent<NekoNpcVoice>(speaker);
        }
        var go = voice.gameObject;
        var backingBefore = UdonSharpEditorUtility.GetBackingUdonBehaviour(voice);
        if (backingBefore == null || backingBefore.programSource == null)
            typeof(UdonSharpEditorUtility).GetMethod("RunBehaviourSetupWithUndo", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { voice });
        var player = go.GetComponent<VRCAVProVideoPlayer>();
        if (player == null) player = Undo.AddComponent<VRCAVProVideoPlayer>(go);
        player.Loop = false; player.EnableAutomaticResync = false;
        var videoProperties = new SerializedObject(player);
        var lowLatency = videoProperties.FindProperty("useLowLatency");
        if (lowLatency == null) throw new InvalidOperationException("SDK player lacks useLowLatency.");
        lowLatency.boolValue = true;
        var builtInUrl = videoProperties.FindProperty("videoURL.url");
        if (builtInUrl != null) builtInUrl.stringValue = "";
        videoProperties.ApplyModifiedPropertiesWithoutUndo();
        var audio = go.GetComponent<AudioSource>();
        if (audio == null) audio = Undo.AddComponent<AudioSource>(go);
        audio.playOnAwake = false; audio.loop = false; audio.spatialBlend = 1;
        audio.spatialize = true; audio.dopplerLevel = 0; audio.volume = .3f;
        audio.minDistance = .1f; audio.maxDistance = 12f;
        var output = go.GetComponent<VRCAVProVideoSpeaker>();
        if (output == null) output = Undo.AddComponent<VRCAVProVideoSpeaker>(go);
        var speakerProperties = new SerializedObject(output);
        var speakerPlayer = speakerProperties.FindProperty("videoPlayer");
        if (speakerPlayer == null) throw new InvalidOperationException("SDK speaker has no videoPlayer binding.");
        speakerPlayer.objectReferenceValue = player; speakerProperties.ApplyModifiedPropertiesWithoutUndo();
        var spatial = go.GetComponent<VRCSpatialAudioSource>();
        if (spatial == null) spatial = Undo.AddComponent<VRCSpatialAudioSource>(go);
        spatial.Gain = 10; spatial.Near = 0; spatial.Far = 12; spatial.VolumetricRadius = 0;
        spatial.EnableSpatialization = true; spatial.UseAudioSourceVolumeCurve = false;
        voice.videoPlayer = player; voice.voiceSource = audio;
        voice.maxReconnectAttempts = 0;
        voice.telemetry = router.telemetry; voice.router = router;
        voice.fixedStreamUrl = new VRCUrl(defaultUrl);
        router.voice = voice; router.enableVoiceStream = true;
        ConfigurePanel(voice, root);
        UdonSharpEditorUtility.CopyProxyToUdon(voice);
        UdonSharpEditorUtility.CopyProxyToUdon(router);
        EditorUtility.SetDirty(voice); EditorUtility.SetDirty(router);
        EditorSceneManager.MarkSceneDirty(scene);
        if (!EditorSceneManager.SaveScene(scene)) throw new InvalidOperationException("Scene save failed.");
        AssetDatabase.SaveAssets();
        Debug.Log("[NEKO] YUI spatial voice configured. Head audio, low-latency AVPro, shared URL/mute, controller-only setup. No upload performed.");
    }

    static void ConfigurePanel(NekoNpcVoice voice, Transform root)
    {
        var panelObject = GameObject.Find("YuiVoiceSettings");
        if (panelObject == null)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(PlayerPrefab);
            if (prefab == null) throw new InvalidOperationException("SDK AVPro sample is missing.");
            var sampleCanvas = prefab.GetComponentInChildren<Canvas>(true);
            panelObject = UnityEngine.Object.Instantiate(sampleCanvas.gameObject);
            panelObject.name = "YuiVoiceSettings";
            Undo.RegisterCreatedObjectUndo(panelObject, "YUI voice settings");
            panelObject.transform.position = root.position + root.right * -1.15f + Vector3.up * 1.15f;
            panelObject.transform.localScale = Vector3.one * .002f;
        }
        // Unity UI is viewed from local -Z. The NPC's front is local +Z.
        panelObject.transform.rotation = root.rotation * Quaternion.Euler(0f, 180f, 0f);
        var rect = panelObject.GetComponent<RectTransform>(); rect.sizeDelta = new Vector2(760, 260);
        var canvas = panelObject.GetComponent<Canvas>(); canvas.renderMode = RenderMode.WorldSpace;
        var background = panelObject.GetComponent<Image>();
        if (background == null) background = panelObject.AddComponent<Image>();
        background.enabled = false;
        var bodyTransform = panelObject.transform.Find("VoiceSettingsBody");
        var body = bodyTransform == null ? new GameObject("VoiceSettingsBody", typeof(RectTransform), typeof(Image)) : bodyTransform.gameObject;
        body.transform.SetParent(panelObject.transform, false);
        var bodyRect = body.GetComponent<RectTransform>(); bodyRect.anchoredPosition = Vector2.zero; bodyRect.sizeDelta = rect.sizeDelta;
        body.GetComponent<Image>().color = new Color(.035f,.07f,.12f,.94f);
        // Migrate the original URL input/labels while preserving their references.
        foreach (Transform child in panelObject.transform.Cast<Transform>().ToArray())
            if (child != body.transform && child.name != "SettingsToggle") child.SetParent(body.transform, false);
        body.SetActive(true); voice.settingsBody = body;
        var font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/NEKO/Fonts/NotoSansSC-Dynamic SDF.asset");
        var input = panelObject.GetComponentInChildren<VRCUrlInputField>(true);
        if (input == null) throw new InvalidOperationException("Voice URL input is missing.");
        // Do not retain callbacks targeting the SDK sample's unrelated Udon program.
        var inputProperties = new SerializedObject(input);
        var iterator = inputProperties.GetIterator();
        while (iterator.Next(true))
            if (iterator.isArray && iterator.propertyPath.EndsWith("m_PersistentCalls.m_Calls")) iterator.arraySize = 0;
        inputProperties.ApplyModifiedPropertiesWithoutUndo();
        // Pointer clicks still activate editing. Walking/keyboard navigation
        // must not select and activate this field incidentally.
        inputProperties.FindProperty("m_Navigation.m_Mode").intValue = (int)Navigation.Mode.None;
        var activateOnSelect = inputProperties.FindProperty("m_ShouldActivateOnSelect");
        if (activateOnSelect == null) throw new InvalidOperationException("SDK URL field lacks activation control.");
        activateOnSelect.boolValue = false;
        inputProperties.ApplyModifiedPropertiesWithoutUndo();
        var inputRect = input.GetComponent<RectTransform>();
        inputRect.anchorMin = inputRect.anchorMax = new Vector2(.5f,.5f);
        inputRect.anchoredPosition = new Vector2(0,37); inputRect.sizeDelta = new Vector2(720,44);
        input.SetUrl(voice.fixedStreamUrl);
        voice.urlInput = input;
        Label(body.transform,"Title","YUI 语音连接",new Vector2(-90,97),new Vector2(520,40),font,27);
        voice.statusText = Label(body.transform,"Status","粘贴语音服务地址，点击连接。声音从 YUI 头部播放。",new Vector2(0,-16),new Vector2(720,42),font,20);
        var backing = UdonSharpEditorUtility.GetBackingUdonBehaviour(voice);
        input.onEndEdit = new VRCUrlInputField.SubmitEvent();
        UnityEventTools.AddStringPersistentListener(input.onEndEdit,backing.SendCustomEvent,"EndUrlEdit");
        Button(body.transform,"Apply","连接语音",-245,"ApplyUrl",font,backing);
        Button(body.transform,"Mute","静音 / 取消静音",0,"ToggleMute",font,backing);
        Button(body.transform,"Retry","重新连接",245,"RetryVoice",font,backing);
        Button(panelObject.transform,"SettingsToggle","关闭设置",0,"ToggleSettings",font,backing);
        var toggle = panelObject.transform.Find("SettingsToggle");
        var toggleRect = toggle.GetComponent<RectTransform>();
        toggleRect.anchoredPosition = new Vector2(278,97);
        toggleRect.sizeDelta = new Vector2(164,40);
        toggle.Find("Label").GetComponent<RectTransform>().sizeDelta = toggleRect.sizeDelta;
        toggle.SetAsLastSibling();
        voice.settingsToggleText = toggle.GetComponentInChildren<TextMeshProUGUI>();
    }

    static TextMeshProUGUI Label(Transform parent,string name,string content,Vector2 position,Vector2 size,TMP_FontAsset font,int pointSize)
    {
        var child = parent.Find(name);
        var go = child == null ? new GameObject(name,typeof(RectTransform)) : child.gameObject;
        go.transform.SetParent(parent,false);
        var rect = go.GetComponent<RectTransform>(); rect.anchoredPosition=position; rect.sizeDelta=size;
        var text=go.GetComponent<TextMeshProUGUI>(); if(text==null) text=go.AddComponent<TextMeshProUGUI>();
        if(font!=null) text.font=font; text.text=content; text.fontSize=pointSize;
        text.alignment=TextAlignmentOptions.Center; text.color=Color.white; text.raycastTarget=false;
        return text;
    }

    static void Button(Transform parent,string name,string title,float x,string eventName,TMP_FontAsset font,UdonBehaviour backing)
    {
        var child=parent.Find(name);
        var go=child==null?new GameObject(name,typeof(RectTransform),typeof(Image),typeof(UnityEngine.UI.Button)):child.gameObject;
        go.transform.SetParent(parent,false);
        var rect=go.GetComponent<RectTransform>(); rect.anchoredPosition=new Vector2(x,-91); rect.sizeDelta=new Vector2(225,48);
        rect.anchorMin=rect.anchorMax=new Vector2(.5f,.5f);
        go.GetComponent<Image>().color=new Color(.08f,.27f,.39f,1);
        var button=go.GetComponent<UnityEngine.UI.Button>(); button.onClick=new UnityEngine.UI.Button.ButtonClickedEvent();
        button.navigation=new Navigation { mode=Navigation.Mode.None };
        button.targetGraphic=go.GetComponent<Image>();
        UnityEventTools.AddStringPersistentListener(button.onClick,backing.SendCustomEvent,eventName);
        Label(go.transform,"Label",title,Vector2.zero,new Vector2(225,48),font,21);
    }
}
#endif
