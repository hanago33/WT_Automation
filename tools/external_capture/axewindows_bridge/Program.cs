// AxeBridge —— Axe.Windows 的 JSON 桥接器。
//
// 调用 Axe.Windows (AccessibilityInsights 底层引擎) 扫描指定进程的 UIA 树，
// 把每个违规元素的 Properties + Patterns 输出为 JSON，供 Python 侧
// tools/external_capture/axewindows_client.py 解析。
//
// 编译运行（需 .NET 8 SDK）：
//   dotnet run --project tools/external_capture/axewindows_bridge -- <processId>
//
// 输出 JSON 形如：
//   { "processId": 1234, "windowScans": [ { "errorCount": N, "outputFile": "...",
//       "elements": [ { "rule": "...", "properties": {...}, "patterns": [...] } ] } ] }
//
// 说明：Axe.Windows 的 Scan API 返回的是"触发无障碍规则的元素"集合（非整树），
// 因此 elements 是规则违规元素，但每个元素携带完整的 Properties + Patterns，
// 正好用于补充 WT_Automation 评分所需的"控件模式校验"维度。
//
// 许可证：MIT（与上游 axe-windows 一致）。源码见 vendor/axe-windows/。

using System;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using Axe.Windows.Automation;
using Axe.Windows.Automation.Data;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length < 1 || !int.TryParse(args[0], out int processId))
        {
            Console.Error.WriteLine("Usage: AxeBridge <processId>");
            return 2;
        }

        var config = Config.Builder
            .ForProcessId(processId)
            .WithOutputFileFormat(OutputFileFormat.A11yTest)
            .WithAlwaysSaveTestFile()
            .Build();

        var scanner = ScannerFactory.CreateScanner(config);

        // ScanOptions：使用默认值即可；支持通过第二个参数传入 HWND 作为扫描根。
        // 注意：ScanOptions 内部会把 null 的 scanRootWindowHandle 规整为 IntPtr.Zero，
        // 因此即使不传 HWND，也走进程级枚举（等价于无障碍洞察的标准进程扫描）。
        IntPtr? scanRoot = null;
        if (args.Length > 1 && long.TryParse(args[1], out long hwndVal) && hwndVal != 0)
        {
            scanRoot = (IntPtr)hwndVal;
        }
        var scanOptions = new ScanOptions(scanRootWindowHandle: scanRoot);

        var options = new JsonSerializerOptions { WriteIndented = true, ReferenceHandler = ReferenceHandler.IgnoreCycles };

        try
        {
            ScanOutput output = scanner.Scan(scanOptions);

        var doc = new
        {
            processId = processId,
            windowScans = output.WindowScanOutputs.Select(w => new
            {
                errorCount = w.ErrorCount,
                outputFile = w.OutputFile.A11yTest,
                elements = (w.Errors ?? Enumerable.Empty<ScanResult>()).Select(r => {
                    // Axe.Windows.Automation.Data.ElementInfo 包含 Parent 属性，可以用于回溯
                    var pathNames = new System.Collections.Generic.List<string>();
                    var current = r.Element;
                    
                    while (current != null)
                    {
                        string name = "";
                        if (current.Properties != null)
                        {
                            if (current.Properties.TryGetValue("Name", out string nVal) && !string.IsNullOrWhiteSpace(nVal))
                                name = nVal;
                            else if (current.Properties.TryGetValue("ControlType", out string cVal) && !string.IsNullOrWhiteSpace(cVal))
                                name = cVal;
                        }
                        if (string.IsNullOrWhiteSpace(name)) name = "node";
                        
                        pathNames.Insert(0, name.Trim());
                        current = current.Parent;
                    }

                    string uiPath = string.Join(" > ", pathNames);
                    string parentPath = pathNames.Count > 1 ? string.Join(" > ", pathNames.Take(pathNames.Count - 1)) : "";

                    return new
                    {
                        rule = r.Rule?.ID.ToString(),
                        ruleDescription = r.Rule?.Description,
                        howToFix = r.Rule?.HowToFix,
                        properties = r.Element?.Properties,
                        patterns = r.Element?.Patterns,
                        uiPath = uiPath,
                        parentPath = parentPath
                    };
                }).ToList()
            }).ToList()
        };

        Console.WriteLine(JsonSerializer.Serialize(doc, options));
        return 0;
        }
        catch (Exception ex)
        {
            // 友好报错：跨完整性/会话导致 UIA 枚举不到窗口时，给出可操作建议而非崩溃堆栈。
            var errDoc = new
            {
                error = ex.GetType().Name,
                message = ex.Message,
                processId = processId,
                suggestion = "未找到该进程的可见 UI 元素（UIA 枚举为空）。请确认：①输入的 PID 正确且进程存在；"
                           + "②该进程有可见窗口（非后台服务/控制台）；③以管理员身份运行本程序（与 UiaPeek 同权限，"
                           + "否则跨完整性级别无法枚举其 UI 树）；④目标进程与 AxeBridge 处于同一 Windows 会话。"
            };
            Console.WriteLine(JsonSerializer.Serialize(errDoc, options));
            return 1;
        }
    }
}
