/** Options the approve panel hands to the request-approve handler. */
export interface RequestApproveOptions {
  browseOnly?: boolean;
  manualApproval?: boolean;
  destinationKey?: string;
}

/**
 * Options for one approve-panel action.
 *
 * Every action that ends in a download keeps the admin's library pick —
 * including browsing for an alternative release, whose modal hides its own
 * picker. Manual approval downloads nothing, so it carries none.
 */
export const reviewApproveOptions = (
  action: 'approve' | 'browse' | 'manual',
  destinationKey?: string,
): RequestApproveOptions => {
  if (action === 'manual') {
    return { manualApproval: true };
  }
  return action === 'browse' ? { browseOnly: true, destinationKey } : { destinationKey };
};
