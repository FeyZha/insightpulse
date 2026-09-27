import { hierarchy, pack } from 'd3-hierarchy'
import { useState, type CSSProperties, type KeyboardEvent } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { Issue } from '@/lib/domain'
import { clusterIssues } from '@/lib/decision'

type PackedIssue = { issue?: Issue; children?: PackedIssue[] }
const clusterColors = ['#91d5c5', '#86a9e0', '#b1c5df', '#79b6cb', '#9dafcc', '#88b5b3']

export function CategoryIssueBubbles({ issues, onSelect }: { issues: Issue[]; onSelect: (id: string) => void }) {
  const [highlighted, setHighlighted] = useState('')
  const clusters = clusterIssues(issues).map(group => [...group].sort((a, b) => b.reviews - a.reviews))
  const entries = clusters.flatMap((group, cluster) => group.map(issue => ({ issue, cluster })))
  const largest = [...issues].sort((a, b) => b.reviews - a.reviews)[0]
  const active = issues.find(issue => issue.id === highlighted) || largest
  const root = pack<PackedIssue>().size([600, 520]).padding(10)(hierarchy<PackedIssue>({ children: clusters.map(group => ({ children: group.map(issue => ({ issue })) })) })
    .sum(node => node.issue ? Math.max(0, node.issue.reviews) : 0).sort((a, b) => (b.value ?? 0) - (a.value ?? 0)))
  const activate = (event: KeyboardEvent<SVGGElement>, id: string) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(id) }
  }

  return <TooltipProvider delayDuration={120}><div className="category-bubble-map" data-cluster-count={clusters.length}>
    <div className="category-bubble-visual">
      <div className="category-bubble-key"><span>问题分布</span><span>气泡越大，关联评论越多</span></div>
      <svg viewBox="0 0 600 520" role="group" aria-label="类别内问题聚类气泡图，相近问题位于同一圈层，气泡面积表示关联评论数">
        {root.children?.map((cluster, index) => cluster.children && cluster.children.length > 1 && <circle key={index} className="category-cluster"
          cx={cluster.x} cy={cluster.y} r={cluster.r} style={{ '--cluster-color': clusterColors[index % clusterColors.length] } as CSSProperties}/>)}
        {root.leaves().filter(node => node.data.issue).map(node => {
          const issue = node.data.issue!
          const index = entries.findIndex(entry => entry.issue.id === issue.id)
          const cluster = entries[index].cluster
          const color = clusterColors[cluster % clusterColors.length]
          const expanded = node.r >= 65
          const fontSize = Math.min(22, Math.max(16, node.r * .2))
          const lineLength = Math.max(4, Math.floor(node.r * 1.45 / fontSize))
          const chars = Array.from(issue.title)
          const lines = Array.from({ length: Math.min(3, Math.ceil(chars.length / lineLength)) }, (_, line) =>
            chars.slice(line * lineLength, (line + 1) * lineLength).join('') + (line === 2 && chars.length > lineLength * 3 ? '…' : ''))
          const titleY = node.y - (lines.length - 1) * fontSize * .65 - node.r * .2
          return <Tooltip key={issue.id}><TooltipTrigger asChild><g className="category-issue-bubble" data-issue-id={issue.id} data-review-count={issue.reviews} data-active={active?.id === issue.id}
            role="button" tabIndex={0} aria-label={`${issue.title}，${issue.reviews} 条评论，查看原话`}
            onMouseMove={() => setHighlighted(issue.id)}
            onFocus={() => setHighlighted(issue.id)}
            onClick={() => onSelect(issue.id)} onKeyDown={event => activate(event, issue.id)}
            style={{ '--cluster-color': color } as CSSProperties}>
            <circle cx={node.x} cy={node.y} r={node.r} fill={color}/>
            {expanded ? <>
              <text className="bubble-title" x={node.x} y={titleY} style={{ fontSize }}>{lines.map((line, lineIndex) => <tspan key={lineIndex} x={node.x} dy={lineIndex ? fontSize * 1.4 : 0}>{line}</tspan>)}</text>
              <text className="bubble-count" x={node.x} y={node.y + node.r * .32} style={{ fontSize: Math.min(56, node.r * .43) }}>{issue.reviews}</text>
              <text className="bubble-unit" x={node.x} y={node.y + node.r * .56}>条关联评论</text>
            </> : node.r >= 11 && <text className="bubble-index" x={node.x} y={node.y} dominantBaseline="central">{String(index + 1).padStart(2, '0')}</text>}
          </g></TooltipTrigger><TooltipContent className="category-bubble-tooltip" side="top" sideOffset={12} collisionPadding={16}
            onEscapeKeyDown={event => event.stopPropagation()}>
            <strong>{issue.title}</strong><span>{issue.reviews} 条关联评论</span>
          </TooltipContent></Tooltip>
        })}
      </svg>
    </div>
    <section className="category-bubble-sidebar" aria-label="类别内全部问题">
      <header><h3>全部问题 <span>{issues.length}</span></h3><span>关联评论</span></header>
      <div className="category-issue-list">{clusters.map((group, cluster) => <div className="category-issue-group" key={group[0].id} style={{ '--cluster-color': clusterColors[cluster % clusterColors.length] } as CSSProperties}>
        <h4><i/>{group.length > 1 ? `相近问题 · ${group.length}` : '独立问题'}</h4>
        {group.map(issue => {
          const index = entries.findIndex(entry => entry.issue.id === issue.id)
          return <button key={issue.id} className="category-issue-row" data-issue-id={issue.id} data-active={active?.id === issue.id}
            aria-label={`${issue.title}，${issue.reviews} 条评论，查看原话`}
            onMouseMove={() => setHighlighted(issue.id)}
            onFocus={() => setHighlighted(issue.id)} onClick={() => onSelect(issue.id)}>
            <span className="category-issue-number">{String(index + 1).padStart(2, '0')}</span><span className="category-issue-name">{issue.title}</span><strong>{issue.reviews}</strong><ArrowUpRight aria-hidden="true"/>
          </button>
        })}
      </div>)}</div>
    </section>
  </div></TooltipProvider>
}
