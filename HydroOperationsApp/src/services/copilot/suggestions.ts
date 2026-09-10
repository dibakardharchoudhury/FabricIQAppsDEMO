// Pull follow-up prompts out of an answer so the chat can offer them as one-click chips.
// The model is asked to emit an explicit <!--options: [...]--> line; the prose heuristic below is
// the fallback for the Data Agent engine and for edited system prompts that drop the rule.

// Raw HTML is not rendered by react-markdown, so the marker is invisible without stripping.
const OPTIONS_MARKER = /<!--\s*options:\s*(\[[\s\S]*?\])\s*-->/i
const CUE = /\b(next steps?|you can|i can|options|examples?|pick one|choose one|would you like|want me to|try asking|suggestions?)\b/i
const LIST_ITEM = /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/

function clean(raw: string): string {
  return raw
    .replace(/`+/g, '')
    // Only paired/delimited emphasis is removed: bare `_` is load-bearing in ids like power_output.
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/(^|[\s(])\*(\S(?:.*?\S)?)\*(?=[\s).,;:]|$)/g, '$1$2')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^[^A-Za-z0-9"']+/, '')
    .replace(/\s+/g, ' ')
    .replace(/[.;,]+$/, '')
    .trim()
}

/** Follow-up options the answer offered, in order, de-duplicated and capped. */
export function extractSuggestions(markdown: string, limit = 5): string[] {
  const declared = declaredOptions(markdown, limit)
  if (declared.length) return declared

  const lines = stripOptionsMarker(markdown).split('\n')
  const found: string[] = []
  const seen = new Set<string>()
  let collecting = false

  for (const line of lines) {
    const item = line.match(LIST_ITEM)
    if (item) {
      if (!collecting) continue
      const text = clean(item[1])
      // Very long bullets are prose, not prompts; very short ones carry no intent.
      if (text.length < 8 || text.length > 160) continue
      const key = text.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      found.push(text)
      if (found.length >= limit) break
      continue
    }
    if (!line.trim()) continue
    collecting = CUE.test(line)
  }
  return found
}

/** Remove the machine-readable marker before copying or re-displaying an answer. */
export function stripOptionsMarker(markdown: string): string {
  return (markdown ?? '').replace(OPTIONS_MARKER, '').trimEnd()
}

/** The model's declared options. Ignored if the line is malformed — the heuristic then applies. */
function declaredOptions(markdown: string, limit: number): string[] {
  const match = (markdown ?? '').match(OPTIONS_MARKER)
  if (!match) return []
  try {
    const parsed = JSON.parse(match[1]) as unknown
    if (!Array.isArray(parsed)) return []
    const seen = new Set<string>()
    return parsed
      .filter((value): value is string => typeof value === 'string')
      .map(value => clean(value))
      .filter(value => value.length >= 4 && value.length <= 160 && !seen.has(value.toLowerCase()) && seen.add(value.toLowerCase()))
      .slice(0, limit)
  } catch {
    return []
  }
}

/** Chip label: keep the pill small, the full text still goes into the composer. */
export function suggestionLabel(text: string, max = 52): string {
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`
}
