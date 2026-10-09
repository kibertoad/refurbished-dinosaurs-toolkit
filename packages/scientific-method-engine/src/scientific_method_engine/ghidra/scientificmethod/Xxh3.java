package scientificmethod;

/**
 * The 128-bit XXH3 hash with seed 0 and the default secret: the xxh3 the Standard records for a file
 * and the engine compares a source with. It follows the reference algorithm of xxHash 0.8 and
 * returns the 32 lower-case hex digits that xxhash's xxh3_128_hexdigest and xxhsum -H2 print.
 */
public final class Xxh3 {
    private static final long PRIME32_1 = 0x9E3779B1L;
    private static final long PRIME32_2 = 0x85EBCA77L;
    private static final long PRIME32_3 = 0xC2B2AE3DL;
    private static final long PRIME64_1 = 0x9E3779B185EBCA87L;
    private static final long PRIME64_2 = 0xC2B2AE3D27D4EB4FL;
    private static final long PRIME64_3 = 0x165667B19E3779F9L;
    private static final long PRIME64_4 = 0x85EBCA77C2B2AE63L;
    private static final long PRIME64_5 = 0x27D4EB2F165667C5L;
    private static final long PRIME_MX1 = 0x165667919E3779F9L;
    private static final long PRIME_MX2 = 0x9FB21C651E98DF25L;
    private static final int STRIPE = 64;
    private static final int STRIPES_PER_BLOCK = (192 - STRIPE) / 8;
    private static final int BLOCK = STRIPE * STRIPES_PER_BLOCK;
    private static final byte[] SECRET = hex(
        "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671f"
        + "cb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c"
        + "3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8"
        + "a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364"
        + "eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e"
        + "2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e");

    private Xxh3() {
    }

    /** The hash of the bytes as 32 lower-case hex digits, the high half first. */
    public static String hexDigest(byte[] input) {
        long[] h = hash(input);
        return String.format("%016x%016x", h[1], h[0]);
    }

    /** The hash of the bytes as its low and high halves. */
    public static long[] hash(byte[] in) {
        int len = in.length;
        if (len == 0) {
            return new long[] {
                xxh64Avalanche(le64(SECRET, 64) ^ le64(SECRET, 72)),
                xxh64Avalanche(le64(SECRET, 80) ^ le64(SECRET, 88))};
        }
        if (len <= 3) {
            long low = (in[0] & 0xFFL) << 16 | (in[len >> 1] & 0xFFL) << 24 | (in[len - 1] & 0xFFL) | (long) len << 8;
            long high = Integer.toUnsignedLong(Integer.rotateLeft(Integer.reverseBytes((int) low), 13));
            return new long[] {
                xxh64Avalanche(low ^ ((le32(SECRET, 0) ^ le32(SECRET, 4)) & 0xFFFFFFFFL)),
                xxh64Avalanche(high ^ ((le32(SECRET, 8) ^ le32(SECRET, 12)) & 0xFFFFFFFFL))};
        }
        if (len <= 8) {
            long keyed = (le32(in, 0) + (le32(in, len - 4) << 32)) ^ (le64(SECRET, 16) ^ le64(SECRET, 24));
            long multiplier = PRIME64_1 + ((long) len << 2);
            long low = keyed * multiplier;
            long high = Math.unsignedMultiplyHigh(keyed, multiplier);
            high += low << 1;
            low ^= high >>> 3;
            low ^= low >>> 35;
            low *= PRIME_MX2;
            low ^= low >>> 28;
            return new long[] {low, avalanche(high)};
        }
        if (len <= 16) {
            long inputHigh = le64(in, len - 8) ^ (le64(SECRET, 48) ^ le64(SECRET, 56));
            long keyed = le64(in, 0) ^ le64(in, len - 8) ^ (le64(SECRET, 32) ^ le64(SECRET, 40));
            long low = keyed * PRIME64_1;
            long high = Math.unsignedMultiplyHigh(keyed, PRIME64_1);
            low += (long) (len - 1) << 54;
            high += inputHigh + (inputHigh & 0xFFFFFFFFL) * (PRIME32_2 - 1);
            low ^= Long.reverseBytes(high);
            long resultLow = low * PRIME64_2;
            long resultHigh = Math.unsignedMultiplyHigh(low, PRIME64_2) + high * PRIME64_2;
            return new long[] {avalanche(resultLow), avalanche(resultHigh)};
        }
        if (len <= 240) {
            long[] acc = {len * PRIME64_1, 0};
            if (len <= 128) {
                if (len > 32) {
                    if (len > 64) {
                        if (len > 96) {
                            mix32(acc, in, 48, len - 64, 96);
                        }
                        mix32(acc, in, 32, len - 48, 64);
                    }
                    mix32(acc, in, 16, len - 32, 32);
                }
                mix32(acc, in, 0, len - 16, 0);
            } else {
                for (int i = 0; i < 4; i++) {
                    mix32(acc, in, 32 * i, 32 * i + 16, 32 * i);
                }
                acc[0] = avalanche(acc[0]);
                acc[1] = avalanche(acc[1]);
                for (int i = 4; i < len / 32; i++) {
                    mix32(acc, in, 32 * i, 32 * i + 16, 3 + 32 * (i - 4));
                }
                mix32(acc, in, len - 16, len - 32, 136 - 17 - 16);
            }
            long low = acc[0] + acc[1];
            long high = acc[0] * PRIME64_1 + acc[1] * PRIME64_4 + len * PRIME64_2;
            return new long[] {avalanche(low), -avalanche(high)};
        }
        long[] acc = {PRIME32_3, PRIME64_1, PRIME64_2, PRIME64_3, PRIME64_4, PRIME32_2, PRIME64_5, PRIME32_1};
        int blocks = (len - 1) / BLOCK;
        for (int n = 0; n < blocks; n++) {
            for (int s = 0; s < STRIPES_PER_BLOCK; s++) {
                accumulate(acc, in, n * BLOCK + s * STRIPE, s * 8);
            }
            for (int i = 0; i < 8; i++) {
                long a = acc[i];
                a ^= a >>> 47;
                a ^= le64(SECRET, 192 - STRIPE + 8 * i);
                acc[i] = a * PRIME32_1;
            }
        }
        int stripes = ((len - 1) - BLOCK * blocks) / STRIPE;
        for (int s = 0; s < stripes; s++) {
            accumulate(acc, in, blocks * BLOCK + s * STRIPE, s * 8);
        }
        accumulate(acc, in, len - STRIPE, 192 - STRIPE - 7);
        return new long[] {merge(acc, 11, len * PRIME64_1), merge(acc, 192 - STRIPE - 11, ~(len * PRIME64_2))};
    }

    private static void accumulate(long[] acc, byte[] in, int at, int secretAt) {
        for (int i = 0; i < 8; i++) {
            long value = le64(in, at + 8 * i);
            long key = value ^ le64(SECRET, secretAt + 8 * i);
            acc[i ^ 1] += value;
            acc[i] += (key & 0xFFFFFFFFL) * (key >>> 32);
        }
    }

    private static long merge(long[] acc, int secretAt, long start) {
        long result = start;
        for (int i = 0; i < 4; i++) {
            result += fold(acc[2 * i] ^ le64(SECRET, secretAt + 16 * i), acc[2 * i + 1] ^ le64(SECRET, secretAt + 16 * i + 8));
        }
        return avalanche(result);
    }

    private static void mix32(long[] acc, byte[] in, int first, int second, int secretAt) {
        acc[0] += mix16(in, first, secretAt);
        acc[0] ^= le64(in, second) + le64(in, second + 8);
        acc[1] += mix16(in, second, secretAt + 16);
        acc[1] ^= le64(in, first) + le64(in, first + 8);
    }

    private static long mix16(byte[] in, int at, int secretAt) {
        return fold(le64(in, at) ^ le64(SECRET, secretAt), le64(in, at + 8) ^ le64(SECRET, secretAt + 8));
    }

    /** The low and high halves of the 128-bit product, combined by exclusive or. */
    private static long fold(long a, long b) {
        return (a * b) ^ Math.unsignedMultiplyHigh(a, b);
    }

    private static long avalanche(long h) {
        h ^= h >>> 37;
        h *= PRIME_MX1;
        return h ^ (h >>> 32);
    }

    private static long xxh64Avalanche(long h) {
        h ^= h >>> 33;
        h *= PRIME64_2;
        h ^= h >>> 29;
        h *= PRIME64_3;
        return h ^ (h >>> 32);
    }

    private static long le32(byte[] b, int at) {
        return (b[at] & 0xFFL) | (b[at + 1] & 0xFFL) << 8 | (b[at + 2] & 0xFFL) << 16 | (b[at + 3] & 0xFFL) << 24;
    }

    private static long le64(byte[] b, int at) {
        return le32(b, at) | le32(b, at + 4) << 32;
    }

    private static byte[] hex(String text) {
        byte[] bytes = new byte[text.length() / 2];
        for (int i = 0; i < bytes.length; i++) {
            bytes[i] = (byte) Integer.parseInt(text.substring(2 * i, 2 * i + 2), 16);
        }
        return bytes;
    }
}
