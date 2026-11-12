import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { WorkspaceIndex } from "./workspace-index.mjs";

async function writeFixture(root, relativePath, content) {
  const target = path.join(root, relativePath);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, content, "utf8");
}

test("选题汇总全部关联内容，并兼容旧生产记录路径", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "meiling-topic-index-"));
  try {
    await writeFixture(root, "我方资料/我方内容库.csv", [
      "内容ID,选题ID,选题名称,标题,当前状态,内容生产记录路径",
      "内容001,选题001,选题一,内容一,生产中,",
      "内容002,选题001,选题一,内容二,已完成,",
    ].join("\n"));
    await writeFixture(root, "我方资料/选题库.csv", [
      "选题ID,选题名称,当前状态,内容生产记录路径",
      "选题001,选题一,已完成分析,",
      "选题002,选题二,已完成分析,我方资料/我方内容/内容003/内容生产记录.md",
    ].join("\n"));

    const snapshot = await new WorkspaceIndex({ workspaceRoot: root }).build();
    const topics = snapshot.workflows.production.topics;
    const linkedTopic = topics.find((topic) => topic.id === "选题001");
    const legacyTopic = topics.find((topic) => topic.id === "选题002");

    assert.deepEqual(linkedTopic.content_ids, ["内容001", "内容002"]);
    assert.equal(linkedTopic.contents.length, 2);
    assert.equal(linkedTopic.has_created_content, true);
    assert.equal(legacyTopic.has_created_content, true);
    assert.equal(
      snapshot.workflows.production.creation_board.columns[0].items.some((item) => item.topic_id === "选题002"),
      false,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("对标内容按来源分开：账号链路来的与单条建档的", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "meiling-benchmark-source-"));
  try {
    await writeFixture(root, "对标资料/对标账号库.csv", [
      "账号ID,账号名,平台",
      "账号甲,甲账号,小红书",
    ].join("\n"));
    await writeFixture(root, "对标资料/对标内容库.csv", [
      "内容ID,平台,账号ID,账号名,标题",
      "内容001,小红书,账号甲,甲账号,账号链路登记进来的",
      "内容002,小红书,账号乙,乙账号,单条内容直接建档的",
      "内容003,小红书,账号甲,甲账号改过名,账号ID 对上但账号名对不上",
    ].join("\n"));

    const snapshot = await new WorkspaceIndex({ workspaceRoot: root }).build();
    const byId = new Map(snapshot.workflows.insight.benchmark_contents.map((content) => [content.id, content]));

    assert.equal(byId.get("内容001").source, "account");
    assert.equal(byId.get("内容001").source_label, "来自对标账号");
    assert.equal(byId.get("内容002").source, "standalone");
    assert.equal(byId.get("内容002").source_label, "独立建档");
    // 账号名会随作者改名漂移，来源判定只认账号ID。
    assert.equal(byId.get("内容003").source, "account");

    const metrics = snapshot.workflows.overview.metrics;
    assert.equal(metrics.benchmark_contents, 3);
    assert.equal(metrics.account_benchmark_contents, 2);
    assert.equal(metrics.standalone_benchmark_contents, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("推荐选题只投影保存器写出的固定结果文件", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "meiling-recommended-topics-"));
  try {
    const topics = Array.from({ length: 9 }, (_, index) => ({
      rank: index + 1,
      title: `选题 ${index + 1}`,
      theme: "选题怎么定",
      audience: "想做内容的创作者",
      pain: "不知道今天拍什么",
      promise: "拿到一个能直接拍的切口",
      proposition: "选题不是想出来的，是有判断标准的",
      angle: "从选题的判断标准切进去",
      mainline: "内容生产方法",
      goal: "流量",
      basis: index === 0 ? "诊断「内容层」+ 内容441 的签单前出图案例" : "账号方向推导",
      outline: ["一", "二", "三"],
    }));
    await writeFixture(root, "我方资料/推荐选题/推荐选题结果.json", JSON.stringify({
      schema: "meiling-recommended-topics/1",
      generated_at: "2026-11-02T10:00:00+08:00",
      plan_path: "我方资料/我方自媒体规划.md",
      topics,
    }));
    await writeFixture(root, "我方资料/推荐选题/推荐选题结果.md", "# 推荐选题（9 条）\n");

    const snapshot = await new WorkspaceIndex({ workspaceRoot: root }).build();
    const result = snapshot.workflows.production.recommended_topics;

    assert.equal(result.topics.length, 9);
    assert.equal(result.topics[0].title, "选题 1");
    assert.equal(result.topics[0].outline.length, 3);
    // 六要素要原样带到工作台，字段名和「选题分析」一致
    assert.equal(result.topics[0].theme, "选题怎么定");
    assert.equal(result.topics[0].pain, "不知道今天拍什么");
    assert.equal(result.topics[0].promise, "拿到一个能直接拍的切口");
    assert.equal(result.topics[0].proposition, "选题不是想出来的，是有判断标准的");
    assert.equal(result.topics[0].angle, "从选题的判断标准切进去");
    // 出处要一路带到工作台，抽屉里靠它判断这条是不是编的
    assert.equal(result.topics[0].basis, "诊断「内容层」+ 内容441 的签单前出图案例");
    assert.equal(result.topics[1].basis, "账号方向推导");
    // json 结果不进文件浏览列表，只通过推荐选题这一块出现；同目录的文档版要能被翻到。
    assert.equal(
      snapshot.files.some((file) => file.path === "我方资料/推荐选题/推荐选题结果.json"),
      false,
    );
    assert.equal(
      snapshot.files.some((file) => file.path === "我方资料/推荐选题/推荐选题结果.md"),
      true,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
