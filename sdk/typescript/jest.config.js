/** @type {import('jest').Config} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/tests"],
  testMatch: ["**/*.test.ts"],
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          target: "ES2020",
          module: "commonjs",
          moduleResolution: "node",
          strict: true,
          esModuleInterop: true,
          skipLibCheck: true,
        },
      },
    ],
  },
  moduleNameMapper: {
    "^@finos/open-resource-broker$": "<rootDir>/src/index.ts",
    // Map .js imports to their source .ts counterparts (for Jest CJS mode)
    "^(\\.{1,2}/.*)\\.js$": "$1",
  },
};
