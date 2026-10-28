package volumefs

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"

	"github.com/gin-gonic/gin"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

func doUpload(router *gin.Engine, basePath, relPath, content string) *httptest.ResponseRecorder {
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	_ = writer.WriteField("base_path", basePath)
	_ = writer.WriteField("rel_path", relPath)
	part, _ := writer.CreateFormFile("file", filepath.Base(relPath))
	_, _ = part.Write([]byte(content))
	_ = writer.Close()

	w := httptest.NewRecorder()
	httpReq, _ := http.NewRequest(http.MethodPost, "/files/upload", body)
	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	router.ServeHTTP(w, httpReq)
	return w
}

var _ = Describe("UploadFile", func() {
	var (
		router  *gin.Engine
		rootDir string
		volDir  string
	)

	BeforeEach(func() {
		rootDir, _ = newTestEnv()
		volDir = filepath.Join(rootDir, testVolumeBasePath)
		router = newTestRouter()
		router.POST("/files/upload", UploadFile)
	})

	AfterEach(func() {
		os.RemoveAll(rootDir) // nolint
	})

	It("creates the never-mounted volume and parent directories", func() {
		w := doUpload(router, testVolumeBasePath, "outputs/docs/a.txt", "hello")
		Expect(w.Code).To(Equal(http.StatusNoContent))
		Expect(os.ReadFile(filepath.Join(volDir, "outputs", "docs", "a.txt"))).To(Equal([]byte("hello")))
	})

	It("overwrites an existing file", func() {
		Expect(os.MkdirAll(volDir, 0o755)).To(Succeed())
		Expect(os.WriteFile(filepath.Join(volDir, "a.txt"), []byte("old"), 0o644)).To(Succeed())

		w := doUpload(router, testVolumeBasePath, "a.txt", "new")
		Expect(w.Code).To(Equal(http.StatusNoContent))
		Expect(os.ReadFile(filepath.Join(volDir, "a.txt"))).To(Equal([]byte("new")))

		entries, err := os.ReadDir(volDir)
		Expect(err).NotTo(HaveOccurred())
		Expect(entries).To(HaveLen(1), "temporary upload file should not be left behind")
	})

	It("rejects a base_path that is not a volume path", func() {
		w := doUpload(router, "app/not-a-volume", "a.txt", "x")
		Expect(w.Code).To(Equal(http.StatusBadRequest))
	})

	It("refuses to overwrite a directory", func() {
		Expect(os.MkdirAll(filepath.Join(volDir, "adir"), 0o755)).To(Succeed())

		w := doUpload(router, testVolumeBasePath, "adir", "x")
		Expect(w.Code).To(Equal(http.StatusBadRequest))
	})

	It("rejects a jail escape with 403", func() {
		w := doUpload(router, testVolumeBasePath, "../../etc/passwd", "x")
		Expect(w.Code).To(Equal(http.StatusForbidden))
		_, err := os.Stat(volDir)
		Expect(os.IsNotExist(err)).To(BeTrue(), "a rejected request must not create the volume directory")
	})

	It("refuses to write through a parent symlink pointing outside the volume", func() {
		outside := filepath.Join(rootDir, "outside")
		Expect(os.MkdirAll(outside, 0o755)).To(Succeed())
		Expect(os.MkdirAll(volDir, 0o755)).To(Succeed())
		Expect(os.Symlink(outside, filepath.Join(volDir, "escape"))).To(Succeed())

		w := doUpload(router, testVolumeBasePath, "escape/secret.txt", "x")
		Expect(w.Code).To(Equal(http.StatusBadRequest))
		_, err := os.Stat(filepath.Join(outside, "secret.txt"))
		Expect(os.IsNotExist(err)).To(BeTrue())
	})

	It("returns 413 when the file exceeds uploadMaxSize", func() {
		orig := uploadMaxSize
		uploadMaxSize = 4
		defer func() { uploadMaxSize = orig }()

		w := doUpload(router, testVolumeBasePath, "big.txt", "hello world")
		Expect(w.Code).To(Equal(http.StatusRequestEntityTooLarge))
	})
})
