export const PASSWORD_MIN_LENGTH = 8;

const COGNITO_SYMBOLS = "^$*.[]{}()?\"!@#%&/\\,><':;|_~`=+-";

export const PASSWORD_POLICY_HINT =
  "Use at least 8 characters with uppercase, lowercase, a number and a symbol.";

export function meetsCognitoPasswordPolicy(password: string) {
  const hasInteriorSpace = password.slice(1, -1).includes(" ");
  const hasSupportedSymbol = [...password].some((character) =>
    COGNITO_SYMBOLS.includes(character),
  );

  return (
    password.length >= PASSWORD_MIN_LENGTH &&
    /[A-Z]/.test(password) &&
    /[a-z]/.test(password) &&
    /[0-9]/.test(password) &&
    (hasSupportedSymbol || hasInteriorSpace)
  );
}
