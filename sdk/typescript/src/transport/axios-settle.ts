/**
 * Replicate Axios's internal `settle` helper so we can use it in the UDS adapter
 * without importing a private symbol from axios internals.
 *
 * settle resolves or rejects the promise based on the response status and the
 * configured validateStatus predicate.
 */
import type { AxiosResponse } from "axios";
import axios from "axios";

export function settle(
  resolve: (value: AxiosResponse) => void,
  reject: (reason: unknown) => void,
  response: AxiosResponse
): void {
  const validateStatus =
    response.config?.validateStatus ?? axios.defaults.validateStatus;
  if (!response.status || !validateStatus || validateStatus(response.status)) {
    resolve(response);
  } else {
    reject(
      new axios.AxiosError(
        `Request failed with status code ${response.status}`,
        [axios.AxiosError.ERR_BAD_RESPONSE, axios.AxiosError.ERR_BAD_REQUEST][
          Math.floor(response.status / 100) - 4
        ] ?? "ERR_BAD_RESPONSE",
        response.config,
        response.request,
        response
      )
    );
  }
}
