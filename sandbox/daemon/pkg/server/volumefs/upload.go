package volumefs

import (
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"os"
	"path/filepath"

	"github.com/gin-gonic/gin"

	"github.com/TencentBlueking/blueking-paas/sandbox/daemon/pkg/config"
	"github.com/TencentBlueking/blueking-paas/sandbox/daemon/pkg/server/httputil"
)

// uploadMaxSize 单文件上限, 与 exportMaxSize 对齐
var uploadMaxSize = exportMaxSize

// UploadFile godoc
//
//	@Summary		Upload a file into a volume
//	@Description	Write the file to base_path/rel_path, creating the volume directory and parent directories when missing; an existing file is overwritten
//	@Tags			pv
//	@Accept			multipart/form-data
//	@Produce		json
//	@Param			base_path	formData	string	true	"volume storage path issued by apiserver, must match app/{32 hex chars}"
//	@Param			rel_path	formData	string	true	"volume-relative destination path"
//	@Param			file		formData	file	true	"File to upload"
//	@Success		204
//	@Router			/files/upload/ [post]
//
//	@id				PVUploadFile
func UploadFile(c *gin.Context) {
	basePath := c.PostForm("base_path")
	relPath := c.PostForm("rel_path")
	if basePath == "" || relPath == "" {
		httputil.BadRequestResponse(c, errors.New("base_path and rel_path are required"))
		return
	}
	// 卷目录可能从未被挂载过, 需要在这里创建, 因此 base_path 必须严格是 Volume 路径
	if err := validateVolumeBasePath(basePath); err != nil {
		httputil.BadRequestResponse(c, err)
		return
	}
	// 建卷目录前先拒绝越界路径, 避免被拒绝的请求也留下卷目录
	if _, err := validateRootPath(relPath); err != nil {
		httputil.ForbiddenResponse(c, err)
		return
	}
	header, err := c.FormFile("file")
	if err != nil {
		httputil.BadRequestResponse(c, err)
		return
	}
	if header.Size > uploadMaxSize {
		httputil.PayloadTooLargeResponse(c, fmt.Errorf("file size %d exceeds limit %d", header.Size, uploadMaxSize))
		return
	}

	if err := ensureVolumeDir(config.G.RootDir, basePath); err != nil {
		respondErr(c, err)
		return
	}
	root, name, ok := openJailedRoot(c, config.G.RootDir, basePath, relPath)
	if !ok {
		return
	}
	defer root.Close() // nolint

	if err := root.MkdirAll(filepath.Dir(name), 0o755); err != nil {
		// MkdirAll 不跟随 symlink: 父路径中有普通文件或 symlink 时返回 ErrExist
		if errors.Is(err, os.ErrExist) {
			httputil.BadRequestResponse(c, fmt.Errorf("parent of %q is not a directory", relPath))
			return
		}
		respondErr(c, err)
		return
	}
	if info, err := root.Stat(name); err == nil && info.IsDir() {
		httputil.BadRequestResponse(c, fmt.Errorf("cannot overwrite a directory: %q", relPath))
		return
	}

	src, err := header.Open()
	if err != nil {
		httputil.BadRequestResponse(c, err)
		return
	}
	defer src.Close() // nolint

	if err := replaceFile(root, name, src); err != nil {
		respondErr(c, err)
		return
	}
	httputil.NoContentResponse(c)
}

// replaceFile 先写入同目录下的临时文件再 rename 覆盖 name, 写入失败时保留原文件。
func replaceFile(root *os.Root, name string, src io.Reader) error {
	tmp := filepath.Join(filepath.Dir(name), fmt.Sprintf(".upload-%016x.tmp", rand.Uint64()))
	dst, err := root.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	_, err = io.Copy(dst, src)
	if closeErr := dst.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = root.Rename(tmp, name)
	}
	if err != nil {
		_ = root.Remove(tmp)
	}
	return err
}
