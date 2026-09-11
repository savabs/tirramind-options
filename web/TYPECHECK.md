`tsc --noEmit` covers `functions/` and `lib/`, the code that is deployed.

The tests are excluded on purpose. `public/positions.js` is a browser module in
plain JavaScript, and inferring its return shapes across that boundary produces
type errors about code that is correct and tested. Vitest runs the tests either
way; type-checking the deployed code is what the compiler is here for.
