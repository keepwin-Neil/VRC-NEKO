// Bounded Windows process-loopback capture. No microphone, desktop mix or game hooks.
// API reference: Microsoft Windows ApplicationLoopback sample (Windows 20348+).
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

[ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioClient
{
    [PreserveSig] int Initialize(int mode, uint flags, long buffer, long period, ref WaveFormat format, IntPtr session);
    [PreserveSig] int GetBufferSize(out uint frames);
    [PreserveSig] int GetStreamLatency(out long latency);
    [PreserveSig] int GetCurrentPadding(out uint padding);
    [PreserveSig] int IsFormatSupported(int mode, IntPtr format, IntPtr closest);
    [PreserveSig] int GetMixFormat(out IntPtr format);
    [PreserveSig] int GetDevicePeriod(out long normal, out long minimum);
    [PreserveSig] int Start();
    [PreserveSig] int Stop();
    [PreserveSig] int Reset();
    [PreserveSig] int SetEventHandle(IntPtr handle);
    [PreserveSig] int GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
}

[ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioCaptureClient
{
    [PreserveSig] int GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong position, out ulong qpc);
    [PreserveSig] int ReleaseBuffer(uint frames);
    [PreserveSig] int GetNextPacketSize(out uint frames);
}

[ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IActivateOperation
{
    [PreserveSig] int GetActivateResult(out int result, [MarshalAs(UnmanagedType.IUnknown)] out object audioInterface);
}

[ComVisible(true), Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IActivateCompletion
{
    [PreserveSig] int ActivateCompleted(IActivateOperation operation);
}

[ComVisible(true), Guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAgileObject { }

[ComVisible(true), ClassInterface(ClassInterfaceType.None)]
public class Activation : IActivateCompletion, IAgileObject
{
    public readonly ManualResetEvent Completed = new ManualResetEvent(false);
    public int Result;
    public object Client;
    public int ActivateCompleted(IActivateOperation operation)
    {
        try { int code; object client; Result = operation.GetActivateResult(out code, out client); if (Result >= 0) Result = code; Client = client; }
        catch (Exception error) { Result = Marshal.GetHRForException(error); }
        finally { Completed.Set(); }
        return 0;
    }
}

[StructLayout(LayoutKind.Sequential, Pack=2)]
struct WaveFormat
{
    public ushort Tag, Channels;
    public uint SamplesPerSecond, BytesPerSecond;
    public ushort BlockAlign, Bits, Extra;
}

[StructLayout(LayoutKind.Explicit, Size=24)]
struct PropVariant
{
    [FieldOffset(0)] public ushort Type;
    [FieldOffset(8)] public uint Size;
    [FieldOffset(16)] public IntPtr Data;
}

class ProcessCapture
{
    [DllImport("Mmdevapi.dll", ExactSpelling=true, CharSet=CharSet.Unicode)]
    static extern int ActivateAudioInterfaceAsync(string path, ref Guid iid, ref PropVariant parameters,
        [MarshalAs(UnmanagedType.Interface)] IActivateCompletion handler, out IActivateOperation operation);

    static string Option(string[] args, string name, string fallback)
    {
        for (int i=0; i+1<args.Length; i++) if (args[i] == name) return args[i+1];
        return fallback;
    }
    static void Check(int result) { if (result < 0) Marshal.ThrowExceptionForHR(result); }
    static void Header(BinaryWriter writer, int bytes)
    {
        writer.BaseStream.Position = 0;
        writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(bytes+36);
        writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16);
        writer.Write((short)1); writer.Write((short)1); writer.Write(16000); writer.Write(32000);
        writer.Write((short)2); writer.Write((short)16);
        writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(bytes);
    }

    [MTAThread]
    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        try
        {
            int seconds = int.Parse(Option(args, "--seconds", "15"));
            if (seconds < 1 || seconds > 20) throw new ArgumentException("duration");
            if (args.Length > 0 && args[0] == "--play-fixture")
            {
                using (var sound = new System.Media.SoundPlayer(args[1]))
                {
                    sound.Load(); Console.WriteLine("ready"); Console.Out.Flush();
                    int delay = int.Parse(Option(args, "--delay-ms", "0"));
                    if (delay < 0 || delay > 3000) throw new ArgumentException("fixture_delay");
                    Thread.Sleep(delay); sound.PlayLooping(); Thread.Sleep(seconds*1000); sound.Stop();
                }
                return 0;
            }
            uint pid = uint.Parse(Option(args, "--pid", "0"));
            using (var target = Process.GetProcessById((int)pid))
            {
                string expected = Option(args, "--require-name", "");
                if (pid == 0 || target.HasExited || (expected.Length > 0 && !String.Equals(target.ProcessName, expected, StringComparison.OrdinalIgnoreCase)))
                    throw new ArgumentException("target_process");
            }
            string output = Path.GetFullPath(Option(args, "--output", ""));
            if (File.Exists(output)) throw new ArgumentException("output_exists");
            var activation = new Activation();
            IActivateOperation operation = null;
            IAudioClient client = null;
            IAudioCaptureClient capture = null;
            IntPtr parameters = Marshal.AllocHGlobal(12);
            bool started = false;
            using (var stop = new ManualResetEvent(false))
            using (var available = new AutoResetEvent(false))
            try
            {
                Marshal.WriteInt32(parameters, 0, 1); // PROCESS_LOOPBACK
                Marshal.WriteInt32(parameters, 4, unchecked((int)pid));
                Marshal.WriteInt32(parameters, 8, 0); // INCLUDE_TARGET_PROCESS_TREE
                var variant = new PropVariant { Type=65, Size=12, Data=parameters };
                Guid iid = typeof(IAudioClient).GUID;
                Check(ActivateAudioInterfaceAsync("VAD\\Process_Loopback", ref iid, ref variant, activation, out operation));
                if (!activation.Completed.WaitOne(4000)) throw new TimeoutException("activation_timeout");
                Check(activation.Result);
                client = (IAudioClient)activation.Client;
                var format = new WaveFormat { Tag=1, Channels=1, SamplesPerSecond=16000, BytesPerSecond=32000, BlockAlign=2, Bits=16, Extra=0 };
                Check(client.Initialize(0, 0x80060000, 0, 0, ref format, IntPtr.Zero));
                object service;
                iid = typeof(IAudioCaptureClient).GUID;
                Check(client.GetService(ref iid, out service));
                capture = (IAudioCaptureClient)service;
                Check(client.SetEventHandle(available.SafeWaitHandle.DangerousGetHandle()));
                using (var stream = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var writer = new BinaryWriter(stream))
                {
                    Header(writer, 0);
                    int bytes = 0, maximum = seconds*32000;
                    Check(client.Start()); started = true;
                    Console.WriteLine("ready"); Console.Out.Flush();
                    var input = new Thread(delegate() { try { Console.ReadLine(); stop.Set(); } catch {} });
                    input.IsBackground = true; input.Start();
                    var clock = Stopwatch.StartNew();
                    while (!stop.WaitOne(0) && clock.Elapsed.TotalSeconds < seconds && bytes < maximum)
                    {
                        available.WaitOne(30);
                        uint count;
                        Check(capture.GetNextPacketSize(out count));
                        while (count > 0)
                        {
                            IntPtr data; uint frames, flags; ulong position, qpc;
                            Check(capture.GetBuffer(out data, out frames, out flags, out position, out qpc));
                            try
                            {
                                int size = Math.Min(checked((int)frames*2), maximum-bytes);
                                var block = new byte[size];
                                if ((flags & 2) == 0 && size > 0) Marshal.Copy(data, block, 0, size);
                                writer.Write(block); bytes += size;
                            }
                            finally { Check(capture.ReleaseBuffer(frames)); }
                            Check(capture.GetNextPacketSize(out count));
                        }
                    }
                    Check(client.Stop()); started = false;
                    Header(writer, bytes); writer.Flush();
                    Console.WriteLine("{\"sample_rate\":16000,\"channels\":1,\"bytes\":"+bytes+"}");
                }
                return 0;
            }
            finally
            {
                if (started && client != null) client.Stop();
                if (capture != null) Marshal.ReleaseComObject(capture);
                if (client != null) Marshal.ReleaseComObject(client);
                if (operation != null) Marshal.ReleaseComObject(operation);
                Marshal.FreeHGlobal(parameters);
                GC.KeepAlive(activation);
            }
        }
        catch (Exception error)
        {
            Console.WriteLine("{\"error\":\""+error.GetType().Name+"\",\"hresult\":\"0x"+Marshal.GetHRForException(error).ToString("x8")+"\"}");
            return 1;
        }
    }
}
