# Qwen Collective — Deliverables & Artifacts User Guide

## 1. Introduction

Welcome to the **Unified Deliverables & Artifacts Hub** of Qwen Collective. This guide explains how to view, preview, download, reuse, and learn from project deliverables produced across the distributed cluster.

---

## 2. Navigating Deliverables

### 2.1 The Global Deliverables Hub (`/deliverables`)
Click the new **Deliverables** link in the top navigation bar to open the cluster-wide asset library:
- **Search Bar**: Instant full-text search across filenames, descriptions, and extracted text.
- **Filters**: Quickly filter by Project, Type (Report, Dataset, Code, Document, Image), Status, or Sort order.
- **Card View**: Displays asset metadata (size, version, checksum preview, producing worker/model, and status badges).

### 2.2 Project Deliverables Tab
Within any Project page (`/projects/{id}`):
1. Click the **Deliverables & Artifacts** tab.
2. The top table displays **Formal Deliverables** awaiting review or accepted, with direct buttons to **Open** (Preview) or **Download**.
3. The lower table displays **Managed Project Assets & Artifacts**, showing every file registered during execution runs.

---

## 3. One-Click Actions on Every Artifact

Opening any artifact takes you to its dedicated detail page (`/deliverables/{artifact_id}`):

### 1. Safe Preview
- **Markdown & Documents**: Rendered cleanly with formatted headers and tables.
- **JSON & Data**: Formatted with indentation and collapsible sections.
- **CSV Spreadsheets**: Rendered into an interactive data table.
- **Source Code**: Syntax-highlighted and sandboxed.

### 2. One-Click Download
Click the **Download** button to download the authentic file with verified SHA-256 integrity.

### 3. "Use in This Project"
Want to continue working on this file in the current project?
Click **Use in This Project** and choose an action:
- **Analyze**: Calculate metrics or inspect structure.
- **Revise**: Request iterative changes or corrections.
- **Expand**: Add sections or extend analysis.
- **Summarize**: Create an executive briefing or key findings summary.
- **Convert**: Translate into another format (e.g. JSON to CSV).
- **Custom**: Type any specific instruction.

### 4. "Create Project From This" (Artifact-Aware Project Creation)
Have a comprehensive research deliverable that you want to turn into an entirely new initiative?
Click **Create Project From This**:
1. Enter your objective: *"Turn this research report into an interactive web app with searchable data."*
2. Qwen Collective creates the new project, automatically links the source deliverable, pulls supporting findings from the knowledge base, and prepares a tailored execution plan!

### 5. "Add to Knowledge"
Click **Add to Knowledge** to extract empirical findings and concept assertions from the artifact. These are linked back to the exact version and made available for future AI planning.

### 6. Version History & Lineage
Scroll to the **Revisions** section to see older versions, or inspect the **Lineage DAG** to see which datasets contributed to this report and what downstream assets have cited it.
