package sse_test

import (
	"bufio"
	"bytes"
	"errors"
	"io"
	"testing"

	"github.com/finos/open-resource-broker/sdk/go/internal/sse"
)

// maxLine mirrors the maximum scanner token size configured by NewReader.
const maxLine = 4 * 1024 * 1024

// hasOversizeLine reports whether data contains a line longer than the
// reader's limit, which the reader must surface through Err().
func hasOversizeLine(data []byte) bool {
	for len(data) > 0 {
		i := bytes.IndexByte(data, '\n')
		if i < 0 {
			return len(data) > maxLine
		}
		if i > maxLine {
			return true
		}
		data = data[i+1:]
	}
	return false
}

func FuzzReader(f *testing.F) {
	seeds := []string{
		"data: a\ndata: b\n\n",
		"data:no-space\n\n",
		"event: ping\nid: 1\nretry: 10\n\n",
		"data: {\"requests\":[]}\r\n\r\n",
		"data: unterminated",
		"\n\n\n\n",
		"",
	}
	for _, s := range seeds {
		f.Add([]byte(s))
	}

	f.Fuzz(func(t *testing.T, data []byte) {
		r := sse.NewReader(io.NopCloser(bytes.NewReader(data)))

		// A frame needs a terminating blank line, which is either "\n\n" or
		// "\n\r\n" (CRLF line endings). The reader consumes at least one
		// byte per call, so len(data)+1 calls is a hard upper bound.
		maxFrames := bytes.Count(data, []byte("\n\n")) + bytes.Count(data, []byte("\n\r\n")) + 1
		frames := 0
		for calls := 0; ; calls++ {
			if calls > len(data)+1 {
				t.Fatalf("reader did not terminate after %d calls", calls)
			}
			frame := r.Next()
			if frame == nil {
				break
			}
			frames++
			if frames > maxFrames {
				t.Fatalf("got %d frames, upper bound is %d", frames, maxFrames)
			}
			// Must never panic on any frame the reader returns.
			sse.IsSentinel(frame)
			sse.FirstRequest(sse.ParsePayload(frame))
		}

		if hasOversizeLine(data) {
			if err := r.Err(); !errors.Is(err, bufio.ErrTooLong) {
				t.Fatalf("oversize line was not reported, Err() = %v", err)
			}
		}
	})
}

func TestReaderOversizeLineSurfacesError(t *testing.T) {
	line := append([]byte("data: "), bytes.Repeat([]byte("x"), maxLine+1)...)
	input := append([]byte("data: {}\n\n"), append(line, '\n', '\n')...)

	r := sse.NewReader(io.NopCloser(bytes.NewReader(input)))
	if f := r.Next(); f == nil || !sse.IsSentinel(f) {
		t.Fatal("expected sentinel frame before the oversize line")
	}
	if f := r.Next(); f != nil {
		t.Fatalf("expected no frame for an oversize line, got %d bytes", len(f.Data))
	}
	if err := r.Err(); !errors.Is(err, bufio.ErrTooLong) {
		t.Fatalf("expected bufio.ErrTooLong, got %v", err)
	}
}
