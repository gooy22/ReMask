// Parse the installed bundle in the same ES-module mode as workspace.php.
// The sentinel stops execution before the application touches DOM or network.
async (src) => {
  const sentinel = 'REMASK_WORKSPACE_PARSE_ONLY';
  const url = URL.createObjectURL(new Blob([
    `throw new Error('${sentinel}');\n`, src
  ], {type: 'text/javascript'}));
  try {
    await import(url);
    return 'Workspace module did not execute its parse guard';
  } catch (error) {
    return error.message === sentinel ? '' : `${error.name}: ${error.message}`;
  } finally {
    URL.revokeObjectURL(url);
  }
};
