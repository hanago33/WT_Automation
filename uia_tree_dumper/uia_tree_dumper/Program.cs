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

    return new FlatControl
    {
        Index          = selfIdx,
        Depth          = depth,
        ParentIndex    = parentIdx,
        ControlType    = cur.ControlType?.ProgrammaticName?.Replace("ControlType.", "") ?? "Unknown",
        Name           = NullIfEmpty(cur.Name),
        AutomationId   = NullIfEmpty(cur.AutomationId),
        ClassName      = NullIfEmpty(cur.ClassName),
        HelpText       = NullIfEmpty(cur.HelpText),
        IsOffscreen    = cur.IsOffscreen,
        IsEnabled      = cur.IsEnabled,
        IsKeyboardFocusable = cur.IsKeyboardFocusable,
        ProcessId      = cur.ProcessId,
        Rect           = rect,
        Value          = NullIfEmpty(value),
        Patterns       = NullIfEmpty(patterns),
        ExpandState    = expandState,
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
    [JsonPropertyName("error")]          public string? Error        { get; init; }
}

record Rect(int X, int Y, int W, int H);
