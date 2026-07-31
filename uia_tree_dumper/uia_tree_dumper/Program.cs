// uia_tree_dumper — walks the full UIA tree via RawViewWalker (no filtering)
// and emits a JSON array of flat control records to stdout.
//
// Usage:
//   uia_tree_dumper --hwnd <decimal_hwnd>
//   uia_tree_dumper --pid  <process_id>
//   uia_tree_dumper --title <window_title_substring>
//   Optional: --maxdepth <n>   (default 40)
//             --timeout <ms>   (default 30000)

using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows.Automation;

// ── argument parsing ──────────────────────────────────────────────────────────
int maxDepth = 40;
int timeoutMs = 30_000;
nint targetHwnd = 0;
int targetPid = 0;
string titleKeyword = "";

for (int i = 0; i < args.Length; i++)
{
    switch (args[i].ToLowerInvariant())
    {
        case "--hwnd"     when i + 1 < args.Length: targetHwnd   = nint.Parse(args[++i]); break;
        case "--pid"      when i + 1 < args.Length: targetPid    = int.Parse(args[++i]);  break;
        case "--title"    when i + 1 < args.Length: titleKeyword = args[++i];             break;
        case "--maxdepth" when i + 1 < args.Length: maxDepth     = int.Parse(args[++i]);  break;
        case "--timeout"  when i + 1 < args.Length: timeoutMs    = int.Parse(args[++i]);  break;
    }
}

// ── locate root AutomationElement ────────────────────────────────────────────
AutomationElement? root = null;
try
{
    if (targetHwnd != 0)
    {
        root = AutomationElement.FromHandle(targetHwnd);
    }
    else if (targetPid != 0)
    {
        root = AutomationElement.RootElement.FindFirst(
            TreeScope.Children,
            new PropertyCondition(AutomationElement.ProcessIdProperty, targetPid));
    }
    else if (!string.IsNullOrWhiteSpace(titleKeyword))
    {
        foreach (AutomationElement child in AutomationElement.RootElement
                     .FindAll(TreeScope.Children, Condition.TrueCondition))
        {
            try
            {
                string t = child.Current.Name ?? "";
                if (t.Contains(titleKeyword, StringComparison.OrdinalIgnoreCase))
                { root = child; break; }
            }
            catch { /* skip inaccessible */ }
        }
    }
}
catch (Exception ex)
{
    Console.Error.WriteLine($"[uia_tree_dumper] Failed to locate root: {ex.Message}");
    Environment.Exit(1);
}

if (root is null)
{
    Console.Error.WriteLine("[uia_tree_dumper] Target window not found.");
    Environment.Exit(2);
}

// ── walk the tree ─────────────────────────────────────────────────────────────
var walker   = TreeWalker.RawViewWalker;
var controls = new List<FlatControl>();
var sw       = Stopwatch.StartNew();

void Walk(AutomationElement el, int depth, int parentIdx)
{
    if (sw.ElapsedMilliseconds > timeoutMs) return;
    if (depth > maxDepth) return;

    var fc = BuildRecord(el, depth, parentIdx, controls.Count);
    int myIdx = controls.Count;
    controls.Add(fc);

    try
    {
        AutomationElement? child = walker.GetFirstChild(el);
        while (child is not null)
        {
            if (sw.ElapsedMilliseconds > timeoutMs) break;
            Walk(child, depth + 1, myIdx);
            try { child = walker.GetNextSibling(child); }
            catch { break; }
        }
    }
    catch { /* container inaccessible — keep what we have */ }
}

Walk(root, 0, -1);

// ── emit JSON to stdout ───────────────────────────────────────────────────────
var opts = new JsonSerializerOptions
{
    WriteIndented      = false,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
};
Console.OutputEncoding = System.Text.Encoding.UTF8;
Console.WriteLine(JsonSerializer.Serialize(controls, opts));

// ── helpers ───────────────────────────────────────────────────────────────────
static FlatControl BuildRecord(AutomationElement el, int depth, int parentIdx, int selfIdx)
{
    AutomationElement.AutomationElementInformation cur;
    try { cur = el.Current; }
    catch
    {
        return new FlatControl
        {
            Index = selfIdx, Depth = depth, ParentIndex = parentIdx,
            ControlType = "Unknown", Error = "inaccessible"
        };
    }

    Rect? rect = null;
    try
    {
        var r = cur.BoundingRectangle;
        if (!r.IsEmpty)
            rect = new Rect((int)r.X, (int)r.Y, (int)r.Width, (int)r.Height);
    }
    catch { }

    string? patterns = null;
    try
    {
        var supported = el.GetSupportedPatterns();
        if (supported.Length > 0)
            patterns = string.Join(",", supported.Select(p => p.ProgrammaticName.Replace("PatternIdentifiers.Pattern", "")));
    }
    catch { }

    bool? expandable = null;
    string? expandState = null;
    try
    {
        if (el.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var pat) && pat is ExpandCollapsePattern ecp)
        {
            expandable   = true;
            expandState  = ecp.Current.ExpandCollapseState.ToString();
        }
    }
    catch { }

    string? value = null;
    try
    {
        if (el.TryGetCurrentPattern(ValuePattern.Pattern, out var pat) && pat is ValuePattern vp)
            value = vp.Current.Value;
    }
    catch { }

    // LabeledBy（WPF Label.Target 权威标签关联）
    string? labeledByName = null;
    try
    {
        var labeledBy = cur.LabeledBy;
        if (labeledBy is not null)
            labeledByName = NullIfEmpty(labeledBy.Current.Name);
    }
    catch { }

    // RuntimeId（与 pywinauto 侧 _format_runtime_id 对齐的十六进制格式）
    string? runtimeId = null;
    try
    {
        var rid = el.GetRuntimeId();
        if (rid is not null && rid.Length > 0)
            runtimeId = "[" + string.Join(",", rid.Select(v => v.ToString("X"))) + "]";
    }
    catch { }

    // 注：LegacyIAccessiblePattern 仅在 .NET Framework 的 UIAutomationClient 中可用，
    // 现代 .NET（net10.0-windows）未提供该 Pattern 封装；MSAA 信息由 Python 采集端
    // 通过 COM LegacyIAccessiblePattern 接口直读补齐，dumper 路径不重复采集。
    string? helpText = NullIfEmpty(cur.HelpText);

    return new FlatControl
    {
        Index          = selfIdx,
        Depth          = depth,
        ParentIndex    = parentIdx,
        ControlType    = cur.ControlType?.ProgrammaticName?.Replace("ControlType.", "") ?? "Unknown",
        Name           = NullIfEmpty(cur.Name),
        AutomationId   = NullIfEmpty(cur.AutomationId),
        ClassName      = NullIfEmpty(cur.ClassName),
        HelpText       = helpText,
        IsOffscreen    = cur.IsOffscreen,
        IsEnabled      = cur.IsEnabled,
        IsKeyboardFocusable = cur.IsKeyboardFocusable,
        ProcessId      = cur.ProcessId,
        Rect           = rect,
        Value          = NullIfEmpty(value),
        Patterns       = NullIfEmpty(patterns),
        ExpandState    = expandState,
        // ── 对齐 Inspect 的补充属性 ──
        LocalizedControlType = NullIfEmpty(cur.LocalizedControlType),
        AccessKey      = NullIfEmpty(cur.AccessKey),
        AcceleratorKey = NullIfEmpty(cur.AcceleratorKey),
        ItemType       = NullIfEmpty(cur.ItemType),
        ItemStatus     = NullIfEmpty(cur.ItemStatus),
        HasKeyboardFocus = cur.HasKeyboardFocus,
        IsContentElement = cur.IsContentElement,
        IsControlElement = cur.IsControlElement,
        IsPassword     = cur.IsPassword,
        FrameworkId    = NullIfEmpty(cur.FrameworkId),
        NativeWindowHandle = cur.NativeWindowHandle != 0 ? "0x" + cur.NativeWindowHandle.ToString("X") : null,
        LabeledByName  = labeledByName,
        RuntimeId      = runtimeId,
    };
}

static string? NullIfEmpty(string? s) => string.IsNullOrWhiteSpace(s) ? null : s;

// ── data model ────────────────────────────────────────────────────────────────
record FlatControl
{
    [JsonPropertyName("index")]          public int     Index        { get; init; }
    [JsonPropertyName("depth")]          public int     Depth        { get; init; }
    [JsonPropertyName("parentIndex")]    public int     ParentIndex  { get; init; }
    [JsonPropertyName("controlType")]    public string  ControlType  { get; init; } = "";
    [JsonPropertyName("name")]           public string? Name         { get; init; }
    [JsonPropertyName("automationId")]   public string? AutomationId { get; init; }
    [JsonPropertyName("className")]      public string? ClassName    { get; init; }
    [JsonPropertyName("helpText")]       public string? HelpText     { get; init; }
    [JsonPropertyName("isOffscreen")]    public bool    IsOffscreen  { get; init; }
    [JsonPropertyName("isEnabled")]      public bool    IsEnabled    { get; init; }
    [JsonPropertyName("isKeyboardFocusable")] public bool IsKeyboardFocusable { get; init; }
    [JsonPropertyName("processId")]      public int     ProcessId    { get; init; }
    [JsonPropertyName("rect")]           public Rect?   Rect         { get; init; }
    [JsonPropertyName("value")]          public string? Value        { get; init; }
    [JsonPropertyName("patterns")]       public string? Patterns     { get; init; }
    [JsonPropertyName("expandState")]    public string? ExpandState  { get; init; }
    [JsonPropertyName("localizedControlType")] public string? LocalizedControlType { get; init; }
    [JsonPropertyName("accessKey")]      public string? AccessKey    { get; init; }
    [JsonPropertyName("acceleratorKey")] public string? AcceleratorKey { get; init; }
    [JsonPropertyName("itemType")]       public string? ItemType     { get; init; }
    [JsonPropertyName("itemStatus")]     public string? ItemStatus   { get; init; }
    [JsonPropertyName("hasKeyboardFocus")] public bool? HasKeyboardFocus { get; init; }
    [JsonPropertyName("isContentElement")] public bool? IsContentElement { get; init; }
    [JsonPropertyName("isControlElement")] public bool? IsControlElement { get; init; }
    [JsonPropertyName("isPassword")]     public bool?   IsPassword   { get; init; }
    [JsonPropertyName("frameworkId")]    public string? FrameworkId  { get; init; }
    [JsonPropertyName("nativeWindowHandle")] public string? NativeWindowHandle { get; init; }
    [JsonPropertyName("labeledByName")]  public string? LabeledByName { get; init; }
    [JsonPropertyName("runtimeId")]      public string? RuntimeId    { get; init; }
    [JsonPropertyName("error")]          public string? Error        { get; init; }
}

record Rect(int X, int Y, int W, int H);
