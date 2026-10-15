/*
 * Windows -> Linux file copy for xfreerdp3, loaded with LD_PRELOAD.
 *
 * When files are copied on Windows, FreeRDP receives a FileGroupDescriptorW
 * list, exposes the files under /tmp/com.freerdp.client.cliprdr.<pid>/<id>/
 * and asks winpr to turn the list into text/uri-list for the file manager.
 * In winpr 3.32 that conversion always fails: the uri-list wrapper passes the
 * *destination* format id to the shared converter, which then rejects it
 * because it is not FileGroupDescriptorW.  Pasting on Linux therefore gets
 * nothing.
 *
 * This library wraps ClipboardGetData.  When winpr returns nothing for a
 * file-list format, it builds the list itself from the descriptors and the
 * exposed path.  If winpr succeeds (a fixed release), it is never involved.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct wClipboard wClipboard;

/* The public head of wClipboardDelegate (winpr/clipboard.h). */
typedef struct {
	wClipboard* clipboard;
	void* custom;
	char* basePath;
} DelegateHead;

typedef void* (*GetDataFn)(wClipboard*, uint32_t, uint32_t*);
typedef uint32_t (*GetFormatIdFn)(wClipboard*, const char*);
typedef DelegateHead* (*GetDelegateFn)(wClipboard*);

#define DESCRIPTOR_SIZE 592 /* sizeof(FILEDESCRIPTORW) */
#define NAME_OFFSET 72      /* offsetof(FILEDESCRIPTORW, cFileName) */
#define NAME_CHARS 260      /* MAX_PATH */

typedef struct {
	char* data;
	size_t len, cap;
} Buffer;

static int put(Buffer* b, const char* s, size_t n)
{
	if (b->len + n + 1 > b->cap) {
		size_t cap = (b->cap ? b->cap * 2 : 256);
		while (cap < b->len + n + 1)
			cap *= 2;
		char* data = realloc(b->data, cap);
		if (!data)
			return 0;
		b->data = data;
		b->cap = cap;
	}
	memcpy(b->data + b->len, s, n);
	b->len += n;
	b->data[b->len] = '\0';
	return 1;
}

static int put_str(Buffer* b, const char* s)
{
	return put(b, s, strlen(s));
}

/* Percent-encode one byte unless it is unreserved or a path separator. */
static int put_encoded(Buffer* b, const unsigned char* s, size_t n)
{
	static const char hex[] = "0123456789ABCDEF";
	for (size_t i = 0; i < n; i++) {
		unsigned char c = s[i];
		if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
		    c == '-' || c == '_' || c == '.' || c == '~' || c == '/') {
			if (!put(b, (const char*)&c, 1))
				return 0;
		} else {
			char esc[3] = { '%', hex[c >> 4], hex[c & 15] };
			if (!put(b, esc, 3))
				return 0;
		}
	}
	return 1;
}

/* UTF-16LE file name (at most NAME_CHARS units) to UTF-8.  Returns length or -1. */
static int name_to_utf8(const unsigned char* src, char* out, size_t outsize)
{
	size_t o = 0;
	for (size_t i = 0; i < NAME_CHARS; i++) {
		uint32_t c = src[2 * i] | (src[2 * i + 1] << 8);
		if (c == 0)
			break;
		if (c >= 0xD800 && c <= 0xDBFF && i + 1 < NAME_CHARS) {
			uint32_t lo = src[2 * i + 2] | (src[2 * i + 3] << 8);
			if (lo >= 0xDC00 && lo <= 0xDFFF) {
				c = 0x10000 + ((c - 0xD800) << 10) + (lo - 0xDC00);
				i++;
			}
		}
		if (o + 4 >= outsize)
			return -1;
		if (c < 0x80) {
			out[o++] = (char)c;
		} else if (c < 0x800) {
			out[o++] = (char)(0xC0 | (c >> 6));
			out[o++] = (char)(0x80 | (c & 0x3F));
		} else if (c < 0x10000) {
			out[o++] = (char)(0xE0 | (c >> 12));
			out[o++] = (char)(0x80 | ((c >> 6) & 0x3F));
			out[o++] = (char)(0x80 | (c & 0x3F));
		} else {
			out[o++] = (char)(0xF0 | (c >> 18));
			out[o++] = (char)(0x80 | ((c >> 12) & 0x3F));
			out[o++] = (char)(0x80 | ((c >> 6) & 0x3F));
			out[o++] = (char)(0x80 | (c & 0x3F));
		}
	}
	out[o] = '\0';
	return (int)o;
}

/*
 * Build the list the way winpr would: one file:// URI per top-level entry
 * (entries inside a copied folder are reached through the folder itself).
 */
static char* build_list(const unsigned char* data, uint32_t size, const char* base,
                        const char* header, const char* ending, int skip_last_ending,
                        uint32_t* pSize)
{
	if (size < 4)
		return NULL;
	uint32_t count = data[0] | (data[1] << 8) | (data[2] << 16) | ((uint32_t)data[3] << 24);
	if (count == 0 || (size - 4) / DESCRIPTOR_SIZE < count)
		return NULL;

	size_t baselen = strlen(base);
	while (baselen > 1 && base[baselen - 1] == '/')
		baselen--;

	Buffer b = { 0 };
	int lines = 0;
	if (!put_str(&b, header))
		goto fail;
	for (uint32_t i = 0; i < count; i++) {
		const unsigned char* name = data + 4 + (size_t)i * DESCRIPTOR_SIZE + NAME_OFFSET;
		char utf8[NAME_CHARS * 4 + 1];
		int n = name_to_utf8(name, utf8, sizeof(utf8));
		if (n <= 0 || strchr(utf8, '\\'))
			continue;
		if (lines++ && !put_str(&b, ending))
			goto fail;
		if (!put_str(&b, "file://") || !put_encoded(&b, (const unsigned char*)base, baselen) ||
		    !put_str(&b, "/") || !put_encoded(&b, (const unsigned char*)utf8, (size_t)n))
			goto fail;
	}
	if (lines == 0)
		goto fail;
	if (!skip_last_ending && !put_str(&b, ending))
		goto fail;
	*pSize = (uint32_t)b.len;
	return b.data;
fail:
	free(b.data);
	return NULL;
}

void* ClipboardGetData(wClipboard* clipboard, uint32_t formatId, uint32_t* pSize)
{
	static GetDataFn real_get;
	static GetFormatIdFn get_id;
	static GetDelegateFn get_delegate;
	if (!real_get) {
		real_get = (GetDataFn)dlsym(RTLD_NEXT, "ClipboardGetData");
		get_id = (GetFormatIdFn)dlsym(RTLD_NEXT, "ClipboardGetFormatId");
		get_delegate = (GetDelegateFn)dlsym(RTLD_NEXT, "ClipboardGetDelegate");
		if (!real_get)
			return NULL;
	}

	void* result = real_get(clipboard, formatId, pSize);
	if (result || !clipboard || !pSize || !get_id || !get_delegate)
		return result;

	const char* header;
	const char* ending;
	int skip_last;
	if (formatId == get_id(clipboard, "text/uri-list")) {
		header = "";
		ending = "\r\n";
		skip_last = 0;
	} else if (formatId == get_id(clipboard, "x-special/gnome-copied-files") ||
	           formatId == get_id(clipboard, "x-special/mate-copied-files")) {
		header = "copy\n";
		ending = "\n";
		skip_last = 1;
	} else {
		return NULL;
	}

	DelegateHead* delegate = get_delegate(clipboard);
	if (!delegate || !delegate->basePath || !delegate->basePath[0])
		return NULL;

	uint32_t size = 0;
	unsigned char* list = real_get(clipboard, get_id(clipboard, "FileGroupDescriptorW"), &size);
	if (!list)
		return NULL;
	result = build_list(list, size, delegate->basePath, header, ending, skip_last, pSize);
	free(list);
	if (!result)
		*pSize = 0;
	return result;
}
