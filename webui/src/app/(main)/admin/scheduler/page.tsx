"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarClock,
  CheckCircle2,
  FileText,
  Loader2,
  PlayCircle,
  RefreshCw,
  TimerReset,
  XCircle,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { useAsyncResource } from "@/hooks/use-async-resource";
import { PageError } from "@/components/layout/page-state";
import { api, type SchedulerJobItem, type SchedulerJobRun } from "@/lib/api";

function formatTimestamp(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const d = new Date(seconds * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function formatDuration(startSec: number, endSec: number | null): string {
  if (!endSec) return "—";
  const ms = (endSec - startSec) * 1000;
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 60_000)} min`;
}

// 把后端的 summary 字段名翻译成中文，已知的列在前面
const SUMMARY_LABELS: Record<string, string> = {
  scanned: "扫描",
  disabled: "禁用",
  deleted: "删除",
  failed: "失败",
  sent: "发送",
  success: "成功",
  active: "活跃",
  total: "总数",
  registered: "注册",
  user_limit: "上限",
  available_regcodes: "可用注册码",
  in_group: "仍在群",
  active_sessions: "活跃会话",
  emby_online: "Emby 在线",
  enabled: "启用",
  days_threshold: "阈值(天)",
};

function formatSummaryValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function renderSummaryChips(summary: SchedulerJobRun["summary"]) {
  if (!summary || typeof summary !== "object") return null;
  const entries = Object.entries(summary);
  if (entries.length === 0) return null;

  // 按已知键的顺序排（其余追加）
  const knownOrder = Object.keys(SUMMARY_LABELS);
  entries.sort(([a], [b]) => {
    const ia = knownOrder.indexOf(a);
    const ib = knownOrder.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <Badge key={key} variant="outline" className="text-[10px] font-normal">
          {SUMMARY_LABELS[key] || key}：{formatSummaryValue(value)}
        </Badge>
      ))}
    </div>
  );
}

function StatusBadge({ job }: { job: SchedulerJobItem }) {
  if (job.is_running || job.last_run?.status === "running") {
    return (
      <Badge variant="outline" className="text-[10px] border-sky-500/40 text-sky-600 dark:text-sky-400">
        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        运行中
      </Badge>
    );
  }
  if (!job.last_run) {
    return (
      <Badge variant="outline" className="text-[10px] text-muted-foreground">
        未运行
      </Badge>
    );
  }
  if (job.last_run.status === "success") {
    return (
      <Badge variant="success" className="text-[10px]">
        <CheckCircle2 className="mr-1 h-3 w-3" />
        上次成功
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="text-[10px]">
      <XCircle className="mr-1 h-3 w-3" />
      上次失败
    </Badge>
  );
}

export default function AdminSchedulerPage() {
  const { toast } = useToast();
  const [jobs, setJobs] = useState<SchedulerJobItem[]>([]);
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const pollTimerRef = useRef<number | null>(null);

  // 日志/历史弹窗
  const [logsJob, setLogsJob] = useState<SchedulerJobItem | null>(null);
  const [logsDetail, setLogsDetail] = useState<SchedulerJobRun | null>(null);
  const [logsHistory, setLogsHistory] = useState<SchedulerJobRun[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const loadJobs = useCallback(async () => {
    const res = await api.listSchedulerJobs();
    if (res.success && res.data) {
      setJobs(res.data.jobs || []);
    }
    return true;
  }, []);

  const {
    isLoading,
    error,
    execute: refresh,
  } = useAsyncResource(loadJobs, { immediate: true });

  const anyRunning = useMemo(
    () => jobs.some((j) => j.is_running || j.last_run?.status === "running") || Object.values(running).some(Boolean),
    [jobs, running]
  );

  useEffect(() => {
    if (!anyRunning) {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      return;
    }
    if (pollTimerRef.current) return;
    pollTimerRef.current = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [anyRunning, refresh]);

  const handleTrigger = async (job: SchedulerJobItem) => {
    setRunning((p) => ({ ...p, [job.id]: true }));
    try {
      const res = await api.triggerSchedulerJob(job.id);
      if (res.success) {
        toast({
          title: `已触发：${job.name}`,
          description: "任务在后台执行，可在卡片中查看状态",
          variant: "success",
        });
        await refresh();
      } else {
        toast({ title: "触发失败", description: res.message, variant: "destructive" });
      }
    } catch (err: any) {
      toast({ title: "触发失败", description: err.message || "网络异常", variant: "destructive" });
    } finally {
      setRunning((p) => ({ ...p, [job.id]: false }));
    }
  };

  const openLogs = async (job: SchedulerJobItem) => {
    setLogsJob(job);
    setLogsDetail(null);
    setLogsHistory([]);
    setLogsLoading(true);
    try {
      const [detailRes, historyRes] = await Promise.all([
        api.getSchedulerJobLastRun(job.id),
        api.getSchedulerJobHistory(job.id, 20),
      ]);
      if (detailRes.success) {
        setLogsDetail(detailRes.data?.last_run || null);
      }
      if (historyRes.success) {
        setLogsHistory(historyRes.data?.history || []);
      }
    } catch (err: any) {
      toast({ title: "加载日志失败", description: err.message || "网络异常", variant: "destructive" });
    } finally {
      setLogsLoading(false);
    }
  };

  if (error) {
    return <PageError message={error} onRetry={() => void refresh()} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold sm:text-3xl">定时任务</h1>
          <p className="text-sm text-muted-foreground">
            手动触发后台定时任务并查看最近一次的执行情况。任务在后台异步执行，本页面会自动轮询状态。
          </p>
        </div>
        <Button variant="outline" onClick={() => void refresh()} disabled={isLoading}>
          {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          刷新
        </Button>
      </div>

      {jobs.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            {isLoading ? "加载中..." : "没有可用的定时任务"}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {jobs.map((job) => {
            const lr = job.last_run;
            const triggering = Boolean(running[job.id]);
            const isRunning = job.is_running || lr?.status === "running" || triggering;
            return (
              <Card key={job.id} className="flex flex-col">
                <CardHeader className="space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <CardTitle className="text-base">{job.name}</CardTitle>
                    <StatusBadge job={job} />
                  </div>
                  <CardDescription className="break-words">
                    {job.description}
                  </CardDescription>
                </CardHeader>
                <CardContent className="mt-auto space-y-3">
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div className="flex items-center gap-2">
                      <CalendarClock className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">
                        计划：{job.enabled ? job.schedule || "已注册" : "未启用"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <TimerReset className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">下次执行：{formatTimestamp(job.next_run_at)}</span>
                    </div>
                  </div>

                  {lr && (
                    <div className="space-y-0.5 rounded-md border border-border/60 bg-muted/30 p-2 text-xs">
                      <p>
                        <span className="text-muted-foreground">开始：</span>
                        {formatTimestamp(lr.started_at)}
                      </p>
                      <p>
                        <span className="text-muted-foreground">结束：</span>
                        {formatTimestamp(lr.finished_at)}
                      </p>
                      <p>
                        <span className="text-muted-foreground">耗时：</span>
                        {formatDuration(lr.started_at, lr.finished_at)}
                      </p>
                      {lr.trigger && lr.trigger !== "scheduled" && (
                        <p>
                          <span className="text-muted-foreground">触发：</span>
                          {lr.trigger === "manual" ? "手动" : lr.trigger === "startup" ? "启动时" : lr.trigger}
                        </p>
                      )}
                      {lr.error && (
                        <p className="break-words text-destructive">
                          错误：{lr.error}
                        </p>
                      )}
                      {renderSummaryChips(lr.summary)}
                    </div>
                  )}

                  <div className="flex gap-2">
                    <Button
                      onClick={() => void handleTrigger(job)}
                      disabled={isRunning}
                      className="flex-1"
                    >
                      {isRunning ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <PlayCircle className="mr-2 h-4 w-4" />
                      )}
                      {isRunning ? "运行中…" : "立即运行"}
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => void openLogs(job)}
                      title="查看运行日志"
                    >
                      <FileText className="h-4 w-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={Boolean(logsJob)} onOpenChange={(open) => { if (!open) { setLogsJob(null); setLogsDetail(null); setLogsHistory([]); } }}>
        <DialogContent className="max-h-[85vh] w-[92vw] max-w-3xl overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="border-b p-4">
            <DialogTitle>{logsJob?.name} · 运行日志</DialogTitle>
            <DialogDescription className="break-words">
              {logsJob?.description}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[70vh] space-y-4 overflow-y-auto p-4">
            {logsLoading ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
              </div>
            ) : !logsDetail ? (
              <p className="text-center text-sm text-muted-foreground">暂无运行记录</p>
            ) : (
              <>
                <div className="rounded-md border border-border/60 bg-muted/30 p-3 text-xs space-y-1">
                  <p><span className="text-muted-foreground">状态：</span>{logsDetail.status}</p>
                  <p><span className="text-muted-foreground">开始：</span>{formatTimestamp(logsDetail.started_at)}</p>
                  <p><span className="text-muted-foreground">结束：</span>{formatTimestamp(logsDetail.finished_at)}</p>
                  <p><span className="text-muted-foreground">耗时：</span>{formatDuration(logsDetail.started_at, logsDetail.finished_at)}</p>
                  {logsDetail.trigger && (
                    <p><span className="text-muted-foreground">触发：</span>{logsDetail.trigger}</p>
                  )}
                  {logsDetail.error && (
                    <p className="break-words text-destructive">错误：{logsDetail.error}</p>
                  )}
                  {renderSummaryChips(logsDetail.summary)}
                </div>

                {logsDetail.logs && logsDetail.logs.length > 0 ? (
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">最近一次日志</p>
                    <pre className="max-h-72 overflow-auto rounded-md border border-border/60 bg-background p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
                      {logsDetail.logs.join("\n")}
                    </pre>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">本次未产生日志输出</p>
                )}

                {logsHistory.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">历史运行（最近 {logsHistory.length} 次）</p>
                    <div className="space-y-1">
                      {logsHistory.map((run) => (
                        <div
                          key={run.id || `${run.started_at}-${run.status}`}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/40 px-3 py-2 text-xs"
                        >
                          <span className="font-mono text-muted-foreground">
                            {formatTimestamp(run.started_at)}
                          </span>
                          <span className="flex items-center gap-2">
                            <Badge
                              variant={run.status === "success" ? "success" : run.status === "failed" ? "destructive" : "outline"}
                              className="text-[10px]"
                            >
                              {run.status}
                            </Badge>
                            <span className="text-muted-foreground">{formatDuration(run.started_at, run.finished_at)}</span>
                            {run.trigger && run.trigger !== "scheduled" && (
                              <span className="text-muted-foreground">[{run.trigger}]</span>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
