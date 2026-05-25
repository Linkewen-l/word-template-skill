import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type TemplateInfo = {
  name: string;
  file_name: string;
  size_bytes: number;
  updated_at: string;
};

type Artifact = {
  name: string;
  path: string;
  kind: string;
  size_bytes: number;
  updated_at: string;
  downloadable: boolean;
};

type JobStatus = "pending" | "running" | "waiting_answers" | "completed" | "failed";

type ProgressStep = {
  id: string;
  label: string;
  status: "done" | "active" | "pending" | "failed";
};

type JobProgress = {
  percent: number;
  current_step: string;
  steps: ProgressStep[];
  artifact_count: number;
  output_count: number;
  recent_artifacts: Artifact[];
};

type Job = {
  id: string;
  topic: string;
  status: JobStatus;
  stage: string;
  message: string;
  dry_run: boolean;
  output_mode: string | null;
  template_name: string | null;
  topic_dir: string | null;
  questions: string[];
  artifacts: Artifact[];
  error: string | null;
  created_at: string;
  updated_at: string;
  progress?: JobProgress;
};

type TopicInfo = {
  name: string;
  path: string;
  updated_at: string;
  has_notes: boolean;
  has_outputs: boolean;
};

type TopicDetail = {
  name: string;
  path: string;
  materials: Artifact[];
  notes: Artifact[];
  outputs: Artifact[];
};

type JobGroup = {
  topic: string;
  jobs: Job[];
};

const API = "/api";

function App() {
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [topics, setTopics] = useState<TopicInfo[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJobId, setActiveJobId] = useState<string>(() => localStorage.getItem("activeJobId") || "");
  const [expandedJobTopic, setExpandedJobTopic] = useState("");
  const [expandedTopic, setExpandedTopic] = useState("");
  const [topicDetail, setTopicDetail] = useState<TopicDetail | null>(null);
  const [topic, setTopic] = useState("");
  const [concept, setConcept] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [materials, setMaterials] = useState<FileList | null>(null);
  const [answers, setAnswers] = useState<string[]>(["", "", "", "", ""]);
  const [outputMode, setOutputMode] = useState<"draft" | "template">("draft");
  const [notice, setNotice] = useState("");

  const activeJob = useMemo(
    () => jobs.find((job) => job.id === activeJobId) || null,
    [activeJobId, jobs]
  );

  const jobGroups = useMemo<JobGroup[]>(() => {
    const groups = new Map<string, Job[]>();
    jobs.forEach((job) => {
      const key = job.topic || "未命名任务";
      groups.set(key, [...(groups.get(key) || []), job]);
    });
    return Array.from(groups.entries()).map(([groupTopic, groupJobs]) => ({
      topic: groupTopic,
      jobs: groupJobs
    }));
  }, [jobs]);

  useEffect(() => {
    void refreshCatalog();
    void refreshJobs();
  }, []);

  useEffect(() => {
    if (!activeJobId) {
      return;
    }
    localStorage.setItem("activeJobId", activeJobId);
    const timer = window.setInterval(() => {
      void refreshJobs();
    }, 1600);
    return () => window.clearInterval(timer);
  }, [activeJobId]);

  useEffect(() => {
    if (templates.length > 0 && !templateName) {
      setTemplateName(templates[0].name);
    }
  }, [templates, templateName]);

  async function refreshCatalog() {
    const [templateResponse, topicResponse] = await Promise.all([
      fetch(`${API}/templates`),
      fetch(`${API}/topics`)
    ]);
    setTemplates(await templateResponse.json());
    setTopics(await topicResponse.json());
  }

  async function refreshJobs() {
    const response = await fetch(`${API}/jobs`);
    setJobs(await response.json());
  }

  async function openTopicFolder(topicName: string) {
    if (expandedTopic === topicName) {
      setExpandedTopic("");
      setTopicDetail(null);
      return;
    }
    setExpandedTopic(topicName);
    setTopic(topicName);
    const response = await fetch(`${API}/topics/detail?name=${encodeURIComponent(topicName)}`);
    if (response.ok) {
      setTopicDetail(await response.json());
    } else {
      setNotice(await readError(response));
    }
  }

  function toggleJobFolder(groupTopic: string) {
    setExpandedJobTopic((current) => (current === groupTopic ? "" : groupTopic));
  }

  async function startQuestions(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("");
    if (!materials || materials.length === 0) {
      setNotice("请至少选择一个材料文件。");
      return;
    }

    const formData = new FormData();
    formData.append("topic", topic);
    formData.append("concept", concept);
    formData.append("template_name", templateName);
    formData.append("dry_run", String(dryRun));
    Array.from(materials).forEach((file) => formData.append("materials", file));

    const response = await fetch(`${API}/workflows/questions`, {
      method: "POST",
      body: formData
    });
    if (!response.ok) {
      setNotice(await readError(response));
      return;
    }
    const payload = await response.json();
    setActiveJobId(payload.job_id);
    setExpandedJobTopic(topic);
    setAnswers(["", "", "", "", ""]);
    await refreshJobs();
    await refreshCatalog();
  }

  async function submitAnswers(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeJob) {
      return;
    }
    const response = await fetch(`${API}/workflows/${activeJob.id}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answers,
        output_mode: outputMode,
        template_name: outputMode === "template" ? templateName : null,
        dry_run: dryRun
      })
    });
    if (!response.ok) {
      setNotice(await readError(response));
      return;
    }
    await refreshJobs();
    await refreshCatalog();
  }

  const canSubmitAnswers = activeJob?.status === "waiting_answers";

  return (
    <main className="app-shell">
      <aside className="side-panel">
        <div>
          <p className="eyebrow">Word Template Skill</p>
          <h1>专利与 Word 模板生成</h1>
        </div>

        <section className="panel-section">
          <h2>最近任务</h2>
          <div className="folder-list">
            {jobGroups.length === 0 ? (
              <p className="muted">暂无任务</p>
            ) : (
              jobGroups.map((group) => (
                <div className="folder-block" key={group.topic}>
                  <button
                    className={expandedJobTopic === group.topic ? "folder active" : "folder"}
                    type="button"
                    onClick={() => toggleJobFolder(group.topic)}
                  >
                    <span className="folder-icon">{expandedJobTopic === group.topic ? "▾" : "▸"}</span>
                    <span className="folder-name">{group.topic}</span>
                    <small>{group.jobs.length} 个任务</small>
                  </button>
                  {expandedJobTopic === group.topic ? (
                    <div className="folder-children">
                      {group.jobs.map((job) => (
                        <button
                          className={job.id === activeJobId ? "child-row active" : "child-row"}
                          key={job.id}
                          type="button"
                          onClick={() => setActiveJobId(job.id)}
                        >
                          <span>{job.status}</span>
                          <small>{job.updated_at}</small>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </section>

        <section className="panel-section">
          <h2>主题工作区</h2>
          <div className="folder-list">
            {topics.slice(0, 12).map((item) => (
              <div className="folder-block" key={item.path}>
                <button
                  className={expandedTopic === item.name ? "folder active" : "folder"}
                  type="button"
                  onClick={() => void openTopicFolder(item.name)}
                >
                  <span className="folder-icon">{expandedTopic === item.name ? "▾" : "▸"}</span>
                  <span className="folder-name">{item.name}</span>
                  <small>{item.has_outputs ? "有输出" : item.has_notes ? "有笔记" : "空"}</small>
                </button>
                {expandedTopic === item.name && topicDetail ? (
                  <div className="folder-children">
                    <FolderSummary label="materials" count={topicDetail.materials.length} />
                    <FolderSummary label="notes" count={topicDetail.notes.length} />
                    <FolderSummary label="outputs" count={topicDetail.outputs.length} />
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </aside>

      <section className="workspace">
        <form className="workflow-form" onSubmit={startQuestions}>
          <div className="section-header">
            <div>
              <p className="eyebrow">Step 1</p>
              <h2>上传材料并生成 5 个问题</h2>
            </div>
            <label className="toggle">
              <input checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} type="checkbox" />
              <span>Dry run</span>
            </label>
          </div>

          <div className="field-grid">
            <label>
              主题
              <input
                required
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="例如：基于频域掩码与时序融合的液压泵故障诊断方法"
              />
            </label>
            <label>
              模板
              <select value={templateName} onChange={(event) => setTemplateName(event.target.value)}>
                {templates.map((template) => (
                  <option key={template.file_name} value={template.name}>
                    {template.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label>
            专利构想
            <textarea
              required
              rows={5}
              value={concept}
              onChange={(event) => setConcept(event.target.value)}
              placeholder="描述要保护的核心技术方案、输入输出、关键模块和希望突出的技术效果。"
            />
          </label>

          <div className="file-row">
            <label>
              材料文件
              <input multiple onChange={(event) => setMaterials(event.target.files)} type="file" />
            </label>
            <button className="primary" type="submit">
              生成问题
            </button>
          </div>
          {notice ? <p className="notice">{notice}</p> : null}
        </form>

        {topicDetail ? <TopicContents detail={topicDetail} /> : null}

        <section className="status-band">
          <div>
            <p className="eyebrow">Current Job</p>
            <h2>{activeJob ? activeJob.topic : "未选择任务"}</h2>
          </div>
          <div className={`status-pill ${activeJob?.status || "idle"}`}>{activeJob?.status || "idle"}</div>
          <p>{activeJob?.message || "提交材料后会在这里显示任务进度。"}</p>
          {activeJob?.progress ? <ProgressView progress={activeJob.progress} /> : null}
          {activeJob?.error ? <pre className="error-box">{activeJob.error}</pre> : null}
        </section>

        {activeJob?.questions.length ? (
          <form className="answers-form" onSubmit={submitAnswers}>
            <div className="section-header">
              <div>
                <p className="eyebrow">Step 2</p>
                <h2>回答 5 个澄清问题</h2>
              </div>
              <select value={outputMode} onChange={(event) => setOutputMode(event.target.value as "draft" | "template")}>
                <option value="draft">草稿 md</option>
                <option value="template">模板 Word</option>
              </select>
            </div>
            <div className="question-stack">
              {activeJob.questions.slice(0, 5).map((question, index) => (
                <label key={`${question}-${index}`}>
                  <span>{index + 1}. {question}</span>
                  <textarea
                    required
                    rows={3}
                    value={answers[index]}
                    disabled={!canSubmitAnswers}
                    onChange={(event) => {
                      const next = [...answers];
                      next[index] = event.target.value;
                      setAnswers(next);
                    }}
                  />
                </label>
              ))}
            </div>
            <button className="primary" disabled={!canSubmitAnswers} type="submit">
              生成结果
            </button>
          </form>
        ) : null}

        {activeJob?.artifacts.length ? <Artifacts title="当前任务产物" artifacts={activeJob.artifacts} /> : null}
      </section>
    </main>
  );
}

function ProgressView({ progress }: { progress: JobProgress }) {
  return (
    <div className="progress-panel">
      <div className="progress-head">
        <span>{progress.current_step}</span>
        <strong>{progress.percent}%</strong>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${progress.percent}%` }} />
      </div>
      <div className="step-timeline">
        {progress.steps.map((step) => (
          <div className={`timeline-step ${step.status}`} key={step.id}>
            <span className="step-dot" />
            <span>{step.label}</span>
          </div>
        ))}
      </div>
      <div className="progress-meta">
        <span>已发现 {progress.artifact_count} 个过程文件</span>
        <span>输出 {progress.output_count} 个</span>
      </div>
      {progress.recent_artifacts.length ? (
        <div className="recent-files">
          <p>最近生成</p>
          {progress.recent_artifacts.map((artifact) => (
            <a href={`${API}/artifacts/download?path=${encodeURIComponent(artifact.path)}`} key={artifact.path}>
              {artifact.name}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FolderSummary({ label, count }: { label: string; count: number }) {
  return (
    <div className="folder-summary">
      <span>{label}</span>
      <small>{count}</small>
    </div>
  );
}

function TopicContents({ detail }: { detail: TopicDetail }) {
  return (
    <section className="artifact-table">
      <div className="section-header">
        <div>
          <p className="eyebrow">Topic Folder</p>
          <h2>{detail.name}</h2>
        </div>
      </div>
      <Artifacts title="materials" artifacts={detail.materials} compact />
      <Artifacts title="notes" artifacts={detail.notes} compact />
      <Artifacts title="outputs" artifacts={detail.outputs} compact />
    </section>
  );
}

function Artifacts({
  artifacts,
  title,
  compact = false
}: {
  artifacts: Artifact[];
  title: string;
  compact?: boolean;
}) {
  return (
    <section className={compact ? "artifact-section compact" : "artifact-section"}>
      <div className="section-header">
        <div>
          <p className="eyebrow">Files</p>
          <h2>{title}</h2>
        </div>
      </div>
      {artifacts.length === 0 ? (
        <p className="muted light">暂无文件</p>
      ) : (
        <div className="table">
          {artifacts.map((artifact) => (
            <div className="table-row" key={artifact.path}>
              <span>{artifact.name}</span>
              <small>{artifact.kind}</small>
              <small>{formatBytes(artifact.size_bytes)}</small>
              {artifact.downloadable ? (
                <a href={`${API}/artifacts/download?path=${encodeURIComponent(artifact.path)}`}>下载</a>
              ) : (
                <span />
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

async function readError(response: Response) {
  try {
    const payload = await response.json();
    return payload.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);
