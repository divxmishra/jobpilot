import { render as renderDiscover } from "./discover.js";

// Saved / Shortlisted reuses the job list with a fixed "shortlisted" filter.
export function render(ctx) {
  return renderDiscover(ctx, { fixedView: "shortlisted" });
}
