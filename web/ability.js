export const experienceLevels = {
  1: '参与过', 2: '有具体产出', 3: '能独立负责', 4: '主导并有成果',
};

const experienceAxes = [
  { id: 'research', name: '科研经历' },
  { id: 'competition', name: '竞赛经历' },
  { id: 'coding-project', name: '项目实践' },
  { id: 'internship', name: '实习经历' },
];

export function abilityProfile(profile = {}) {
  const { rankPercent, cet6Score, experienceTags, experienceDepth } = profile;
  const rankKnown = Number.isFinite(rankPercent) && rankPercent >= 0 && rankPercent <= 100;
  const scoreKnown = Number.isInteger(cet6Score) && cet6Score >= 0 && cet6Score <= 710;
  const rankBand = rankKnown ? 1 + [50, 25, 10].filter((threshold) => rankPercent <= threshold).length : null;
  const scoreBand = scoreKnown ? 1 + [425, 500, 600].filter((threshold) => cet6Score >= threshold).length : null;
  return [
    { name: '学业表现', level: rankBand, evidence: rankKnown ? `专业排名前 ${rankPercent}%` : '未填写专业排名' },
    { name: '外语能力', level: scoreBand, evidence: scoreKnown ? `CET-6 · ${cet6Score} 分` : '未填写 CET-6 成绩' },
    ...experienceAxes.map(({ id, name }) => {
      if (experienceTags?.includes(id)) {
        const detail = experienceDepth?.[id];
        if (Number.isInteger(detail) && detail >= 1 && detail <= 4) return { name, level: detail, evidence: experienceLevels[detail] };
        return { name, level: 1, evidence: '已登记，参与程度未填' };
      }
      if (experienceTags?.length === 0) return { name, level: 0, evidence: '已确认暂无相关经历' };
      return { name, level: null, evidence: '未填写该项经历' };
    }),
  ];
}

export function abilityPanel(id) {
  return `<section class="panel ability-panel" id="${id}" aria-labelledby="${id}-heading">
    <div class="dash-card-head"><h2 id="${id}-heading">个人能力雷达</h2><span class="dash-count" data-ability-count></span></div>
    <div class="ability-body">
      <div class="ability-plot"><canvas width="800" height="652" aria-hidden="true"></canvas></div>
      <dl class="ability-evidence" data-ability-evidence></dl>
    </div>
    <details class="ability-rubric"><summary>查看评估口径</summary><ul>
      <li>学业按专业排名划分。前 10% 为 4 档，前 25% 为 3 档，前 50% 为 2 档，其余为 1 档。</li>
      <li>外语按 CET-6 划分。600 分起为 4 档，500 分起为 3 档，425 分起为 2 档，其余为 1 档。</li>
      <li>经历按本人填写的参与程度显示。仅登记经历时为 1 档，确认暂无经历时为 0 档。</li>
      <li>缺失信息不绘制。分档用于整理个人背景，机会的资格要求以对应公告为准。</li>
    </ul></details>
  </section>`;
}

function drawRadar(canvas, axes) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(2, 0, 0, 2, 0, 0);
  ctx.clearRect(0, 0, 400, 326);
  const center = [200, 160];
  const radius = 107;
  const dark = document.documentElement.dataset.theme === 'dark';
  const palette = dark ? {
    even: '#212d46', odd: '#1a2335', grid: '#435375', muted: '#aebbd5', label: '#e5ecff', point: '#a7bffc', area: '#a7bffc38',
  } : {
    even: '#eef4ee', odd: '#f7faf6', grid: '#c6d6cc', muted: '#52665c', label: '#225242', point: '#236756', area: '#47897338',
  };
  const directions = axes.map((_, i) => {
    const angle = i * Math.PI * 2 / axes.length - Math.PI / 2;
    return [Math.cos(angle), Math.sin(angle)];
  });
  const point = (i, distance) => directions[i].map((component, dim) => center[dim] + component * distance);
  const polygon = (points) => {
    ctx.beginPath();
    ctx.moveTo(...points[0]);
    for (const vertex of points.slice(1)) ctx.lineTo(...vertex);
    ctx.closePath();
  };

  for (let level = 4; level >= 1; level -= 1) {
    polygon(axes.map((_, i) => point(i, radius * level / 4)));
    ctx.fillStyle = level % 2 === 0 ? palette.even : palette.odd;
    ctx.fill();
    ctx.strokeStyle = palette.grid;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  for (const [i, axis] of axes.entries()) {
    ctx.beginPath();
    ctx.moveTo(...center);
    ctx.lineTo(...point(i, radius));
    ctx.strokeStyle = palette.grid;
    ctx.stroke();
    const [x, y] = point(i, radius + (i % 3 === 0 ? 23 : 38));
    ctx.font = '650 15px "Microsoft YaHei UI", "PingFang SC", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = axis.level === null ? palette.muted : palette.label;
    ctx.fillText(axis.name, x, y);
  }
  const values = axes.map(({ level }, i) => level === null ? null : point(i, radius * level / 4));
  ctx.strokeStyle = palette.point;
  ctx.lineWidth = 2.4;
  if (values.every(Boolean)) {
    polygon(values);
    ctx.fillStyle = palette.area;
    ctx.fill();
    ctx.stroke();
  } else {
    for (const [i, vertex] of values.entries()) {
      const next = values[(i + 1) % values.length];
      if (!vertex || !next) continue;
      ctx.beginPath();
      ctx.moveTo(...vertex);
      ctx.lineTo(...next);
      ctx.stroke();
    }
  }
  for (const vertex of values.filter(Boolean)) {
    ctx.beginPath();
    ctx.arc(...vertex, 4, 0, Math.PI * 2);
    ctx.fillStyle = palette.point;
    ctx.fill();
  }
}

export function paintAbility(panel, profile) {
  if (!panel) return;
  const axes = abilityProfile(profile ?? {});
  const filled = axes.filter(({ level }) => level !== null).length;
  panel.querySelector('[data-ability-count]').textContent = `${filled} / ${axes.length} 项有资料`;
  const evidence = panel.querySelector('[data-ability-evidence]');
  evidence.replaceChildren(...axes.map(({ name, evidence: basis, level }) => {
    const row = document.createElement('div');
    const label = document.createElement('dt');
    const nameText = document.createElement('span');
    const detail = document.createElement('dd');
    const badge = document.createElement('span');
    nameText.textContent = name;
    badge.className = `badge ${level === null ? 'missing' : 'ability-level'}`;
    badge.textContent = level === null ? '未填写' : `${level} 档`;
    detail.textContent = basis;
    label.append(nameText, badge);
    row.append(label, detail);
    return row;
  }));
  drawRadar(panel.querySelector('canvas'), axes);
}
