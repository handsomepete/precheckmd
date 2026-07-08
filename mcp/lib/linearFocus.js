/**
 * Resolves "TODAY'S LINEAR FOCUS" for the brief.
 *
 * HOME-447: the brief printed "Error: Entity not found: Issue" because the
 * pinned/selected focus issue had been deleted or archived in Linear and
 * nothing validated it existed before rendering. This validates the pinned
 * issue first and falls back to the highest-priority open issue from
 * get_linear_urgent, logging the dangling id for cleanup.
 */

'use strict';

/**
 * @param {object[]} issues - shape like getLinearUrgentIssues()/get_linear_urgent output
 */
function pickHighestPriorityIssue(issues) {
  // Linear priority: 1 = Urgent, 2 = High, 3 = Medium, 4 = Low, 0/null = No priority.
  const ranked = (issues || []).filter((i) => i.priority != null && i.priority > 0);
  if (ranked.length === 0) return null;
  ranked.sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    const aDue = a.due ? new Date(a.due).getTime() : Infinity;
    const bDue = b.due ? new Date(b.due).getTime() : Infinity;
    return aDue - bDue;
  });
  return ranked[0];
}

/**
 * @param {object} opts
 * @param {string|null|undefined} opts.pinnedIssueId - the currently pinned/selected issue id
 * @param {(id: string) => Promise<object|null>} opts.fetchIssueById - resolves to the issue or null/throws if not found
 * @param {() => Promise<object[]>} opts.getUrgentIssues - fallback source, e.g. getLinearUrgentIssues()
 * @param {{ warn: (msg: string) => void }} [opts.logger]
 */
async function resolveLinearFocus({ pinnedIssueId, fetchIssueById, getUrgentIssues, logger = console }) {
  let danglingIssueId = null;

  if (pinnedIssueId) {
    try {
      const issue = await fetchIssueById(pinnedIssueId);
      if (issue) {
        return { issue, source: 'pinned', dangling_issue_id: null };
      }
      danglingIssueId = pinnedIssueId;
    } catch (err) {
      danglingIssueId = pinnedIssueId;
    }
    logger.warn(
      `[linear-focus] pinned issue "${pinnedIssueId}" no longer exists (deleted/archived) — ` +
      `falling back to highest-priority open issue. Clean up this reference in HOME-447.`
    );
  }

  const urgentIssues = await getUrgentIssues();
  const fallback = pickHighestPriorityIssue(urgentIssues);

  return {
    issue: fallback,
    source: fallback ? 'fallback' : 'none',
    dangling_issue_id: danglingIssueId,
  };
}

module.exports = { resolveLinearFocus, pickHighestPriorityIssue };
