# written by Xiaohui Zhao
# 2018-12 
# xiaohui.zhao@accenture.com
import tensorflow as tf


def layer(op):
    def layer_decorated(self, *args, **kwargs):
        name = kwargs.setdefault('name', self.get_unique_name(op.__name__))        
        if len(self.layer_inputs) == 0:
            raise RuntimeError('No input variables found for layers %s' % name)
        elif len(self.layer_inputs) == 1:
            layer_input = self.layer_inputs[0]
        else:
            layer_input = list(self.layer_inputs)            
            
        layer_output = op(self, layer_input, *args, **kwargs)
        
        self.layers[name] = layer_output
        self.feed(layer_output)
        
        return self
    return layer_decorated
    
    
class Model(object):
    def __init__(self, trainable=True):
        self.layers = dict()      
        self.trainable = trainable
        
        self.layer_inputs = []        
        self.setup()
    
    
    def build_loss(self):
        raise NotImplementedError('Must be subclassed.')
    
    
    def setup(self):        
        raise NotImplementedError('Must be subclassed.')
     
    
    @layer
    def embed(self, layer_input, vocabulary_size, embedding_size, name, trainable=True):
        with tf.variable_scope(name) as scope:
            init_embedding = tf.random_uniform_initializer(-1.0, 1.0)
            embeddings = self.make_var('weights', [vocabulary_size, embedding_size], init_embedding, None, trainable)
            shape = tf.shape(layer_input)
            
            reshaped_input = tf.reshape(layer_input, [-1])
            e = tf.nn.embedding_lookup(embeddings, reshaped_input)
            reshaped_e = tf.reshape(e, [shape[0], shape[1], shape[2], embedding_size])
            return reshaped_e
    
    
    @layer
    def bert_embed(self, layer_input, vocab_size, embedding_size=768, use_one_hot_embeddings=False, 
                   initializer_range=0.02, name="embeddings", trainable=False):
        with tf.variable_scope("bert"):
          with tf.variable_scope("embeddings"):
            # Perform embedding lookup on the word ids.
            (embedding_output, embedding_table) = self.embedding_lookup(
                input_ids=layer_input, vocab_size=vocab_size, embedding_size=embedding_size,
                initializer_range=initializer_range,
                word_embedding_name="word_embeddings",
                use_one_hot_embeddings=use_one_hot_embeddings,
                trainable=trainable)
            self.embedding_table = embedding_table # the inherited class need a self.embedding_table variable
            return embedding_output        
    
     
    @layer
    def sepconv(self, layer_input, k_h, k_w, cardinality, compression, name, activation='relu', trainable=True):
        """ customized seperable convolution
        """
        convolve = lambda input, filter: tf.nn.conv2d(input, filter, [1,1,1,1], 'SAME')
        activate = lambda z: tf.nn.relu(z, 'relu')
        with tf.variable_scope(name) as scope:
            init_weights = tf.truncated_normal_initializer(0.0, 0.01)
            init_biases = tf.constant_initializer(0.0)
            regularizer = self.l2_regularizer(self.weight_decay)
            c_i = layer_input.get_shape().as_list()[-1]
            
            layer_output = []
            c = c_i / cardinality / compression
            for _ in range(cardinality):
                a = self.convolution(convolve, activate, layer_input, 1, 1, c_i, c,
                                     init_weights, init_biases, regularizer, trainable, '0_{}'.format(_))                
                a = self.convolution(convolve, activate, a, k_h, k_w, c, c, 
                                     init_weights, init_biases, regularizer, trainable, '1_{}'.format(_))
                a = self.convolution(convolve, activate, a, 1, 1, c, c_i, 
                                     init_weights, init_biases, regularizer, trainable, '2_{}'.format(_))
                layer_output.append(a)
            layer_output = tf.add_n(layer_output)
            return tf.add(layer_output, layer_input)
        
    
    @layer
    def up_sepconv(self, layer_input, k_h, k_w, cardinality, compression, name, activation='relu', trainable=True):
        """ customized upscale seperable convolution
        """
        convolve = lambda input, filter: tf.nn.conv2d(input, filter, [1,1,1,1], 'SAME')
        activate = lambda z: tf.nn.relu(z, 'relu')        
        with tf.variable_scope(name) as scope:
            shape = tf.shape(layer_input)
            h = shape[1]
            w = shape[2]
            layer_input = tf.image.resize_nearest_neighbor(layer_input, [2*h, 2*w])
            init_weights = tf.truncated_normal_initializer(0.0, 0.01)
            init_biases = tf.constant_initializer(0.0)
            regularizer = self.l2_regularizer(self.weight_decay)
            c_i = layer_input.get_shape().as_list()[-1]
            
            layer_output = []
            c = c_i / cardinality / compression
            for _ in range(cardinality):
                a = self.convolution(convolve, activate, layer_input, 1, 1, c_i, c,
                                     init_weights, init_biases, regularizer, trainable, '0_{}'.format(_))                
                a = self.convolution(convolve, activate, a, k_h, k_w, c, c, 
                                     init_weights, init_biases, regularizer, trainable, '1_{}'.format(_))
                a = self.convolution(convolve, activate, a, 1, 1, c, c_i, 
                                     init_weights, init_biases, regularizer, trainable, '2_{}'.format(_))
                layer_output.append(a)
            layer_output = tf.add_n(layer_output)
            return tf.add(layer_output, layer_input)
        
        
    @layer
    def dense_block(self, layer_input, k_h, k_w, c_o, depth, name, activation='relu', trainable=True):
        convolve = lambda input, filter: tf.nn.conv2d(input, filter, [1,1,1,1], 'SAME')
        activate = lambda z: tf.nn.relu(z, 'relu')
        with tf.variable_scope(name) as scope:
            init_weights = tf.truncated_normal_initializer(0.0, 0.01)
            init_biases = tf.constant_initializer(0.0)
            regularizer = self.l2_regularizer(self.weight_decay)  
            
            layer_tmp = layer_input
            for d in range(depth):          
                c_i = layer_tmp.get_shape()[-1]
                a = self.convolution(convolve, activate, layer_tmp, 1, 1, c_i, c_i//2,
                                     init_weights, init_biases, regularizer, trainable)
                
                a = self.convolution(convolve, activate, a, k_h, k_w, c_i, c_o, 
                                     init_weights, init_biases, regularizer, trainable)
                
                layer_tmp = tf.concat([a, layer_input], 3)
                
            return layer_tmp
            
        
    @layer
    def conv(self, layer_input, k_h, k_w, c_o, s_h, s_w, name, activation='relu', trainable=True):
        convolve = lambda input, filter: tf.nn.conv2d(input, filter, [1,s_h,s_w,1], 'SAME')
        
        activate = lambda z: tf.nn.relu(z, 'relu') #if activation == 'relu':
        if activation == 'sigmoid':
            activate = lambda z: tf.nn.sigmoid(z, 'sigmoid')
            
        with tf.variable_scope(name) as scope:
            init_weights = tf.truncated_normal_initializer(0.0, 0.01)
            init_biases = tf.constant_initializer(0.0)
            regularizer = self.l2_regularizer(self.weight_decay)
            c_i = layer_input.get_shape()[-1]
            
            a = self.convolution(convolve, activate, layer_input, k_h, k_w, c_i, c_o, 
                                 init_weights, init_biases, regularizer, trainable)
            return a  
    
    
    @layer
    def up_conv(self, layer_input, k_h, k_w, c_o, s_h, s_w, name, activation='relu', trainable=True):
        convolve = lambda input, filter: tf.nn.conv2d(input, filter, [1,s_h,s_w,1], 'SAME')
        activate = lambda z: tf.nn.relu(z, 'relu')        
        with tf.variable_scope(name) as scope:
            shape = tf.shape(layer_input)
            h = shape[1]
            w = shape[2]
            layer_input = tf.image.resize_nearest_neighbor(layer_input, [2*h, 2*w])
            init_weights = tf.truncated_normal_initializer(0.0, 0.01)
            init_biases = tf.constant_initializer(0.0)
            regularizer = self.l2_regularizer(self.weight_decay)
            c_i = layer_input.get_shape()[-1]
            
            a = self.convolution(convolve, activate, layer_input, k_h, k_w, c_i, c_o, 
                                 init_weights, init_biases, regularizer, trainable)
            return a  
    
    
    @layer
    def concat(self, layer_input, axis, name):
        return tf.concat(layer_input, axis)
    
    
    @layer
    def max_pool(self, layer_input, k_h, k_w, s_h, s_w, name, padding='VALID'):
        return tf.nn.max_pool(layer_input, [1,k_h,k_w,1], [1,s_h,s_w,1], name=name, padding=padding)
    
    
    @layer
    def softmax(self, layer_input, name):
        return tf.nn.softmax(layer_input, name=name)      
    
    
    def embedding_lookup(self, input_ids, vocab_size, embedding_size=768,
                         initializer_range=0.02, word_embedding_name="word_embeddings",
                         use_one_hot_embeddings=False, trainable=False):
        """Looks up words embeddings for id tensor.
        
        Args:
          input_ids: int32 Tensor of shape [batch_size, seq_length] containing word
            ids.
          vocab_size: int. Size of the embedding vocabulary.
          embedding_size: int. Width of the word embeddings.
          initializer_range: float. Embedding initialization range.
          word_embedding_name: string. Name of the embedding table.
          use_one_hot_embeddings: bool. If True, use one-hot method for word
            embeddings. If False, use `tf.nn.embedding_lookup()`. One hot is better
            for TPUs.
        
        Returns:
          float Tensor of shape [batch_size, seq_length, embedding_size].
        """
        # This function assumes that the input is of shape [batch_size, seq_length,
        # num_inputs].
        #
        # If the input is a 2D tensor of shape [batch_size, seq_length], we
        # reshape to [batch_size, seq_length, 1].
        if input_ids.shape.ndims == 3: # originally 2
            input_ids = tf.expand_dims(input_ids, axis=[-1])
        
        embedding_table = tf.get_variable(
            name=word_embedding_name,
            shape=[vocab_size, embedding_size],
            initializer=tf.truncated_normal_initializer(stddev=initializer_range),
            trainable=trainable)
        
        if use_one_hot_embeddings:
            flat_input_ids = tf.reshape(input_ids, [-1])
            one_hot_input_ids = tf.one_hot(flat_input_ids, depth=vocab_size)
            output = tf.matmul(one_hot_input_ids, embedding_table)
        else:
            output = tf.nn.embedding_lookup(embedding_table, input_ids)
        
        input_shape = self.get_shape_list(input_ids)
        
        output = tf.reshape(output,
                            input_shape[0:-1] + [input_shape[-1] * embedding_size])
        return (output, embedding_table)
    
    def get_shape_list(self, tensor, expected_rank=None, name=None):
        """Returns a list of the shape of tensor, preferring static dimensions.
        
        Args:
          tensor: A tf.Tensor object to find the shape of.
          expected_rank: (optional) int. The expected rank of `tensor`. If this is
            specified and the `tensor` has a different rank, and exception will be
            thrown.
          name: Optional name of the tensor for the error message.
        
        Returns:
          A list of dimensions of the shape of tensor. All static dimensions will
          be returned as python integers, and dynamic dimensions will be returned
          as tf.Tensor scalars.
        """
        if name is None:
          name = tensor.name
        
        if expected_rank is not None:
          assert_rank(tensor, expected_rank, name)
        
        shape = tensor.shape.as_list()
        
        non_static_indexes = []
        for (index, dim) in enumerate(shape):
          if dim is None:
            non_static_indexes.append(index)
        
        if not non_static_indexes:
          return shape
        
        dyn_shape = tf.shape(tensor)
        for index in non_static_indexes:
          shape[index] = dyn_shape[index]
        return shape
    
    
    def convolution(self, convolve, activate, input, k_h, k_w, c_i, c_o, init_weights, init_biases, 
                    regularizer, trainable, name=''):   
        kernel = self.make_var('weights'+name, [k_h, k_w, c_i, c_o], init_weights, regularizer, trainable) 
        biases = self.make_var('biases'+name, [c_o], init_biases, None, trainable)
        tf.summary.histogram('w', kernel)
        tf.summary.histogram('b', biases)
        wx = convolve(input, kernel)
        a = activate(tf.nn.bias_add(wx, biases))
        a = tf.contrib.layers.instance_norm(a, center=False, scale=False)
        return a
    
    
    def l2_regularizer(self, weight_decay=0.0005, scope=None):
        def regularizer(tensor):
            with tf.name_scope(scope, default_name='l2_regularizer', values=[tensor]):
                factor = tf.convert_to_tensor(weight_decay, name='weight_decay')
                return tf.multiply(factor, tf.nn.l2_loss(tensor), name='decayed_value')
        return regularizer
    
    
    def make_var(self, name, shape, initializer=None, regularizer=None, trainable=True):
        return tf.get_variable(name, shape, initializer=initializer, regularizer=regularizer, trainable=trainable)      
    
    
    def feed(self, *args):
        assert len(args) != 0
        
        self.layer_inputs = []
        for layer in args:
            if isinstance(layer, str):
                try:
                    layer = self.layers[layer]
                    print(layer)
                except KeyError:
                    print(list(self.layers.keys()))
                    raise KeyError('Unknown layer name fed: %s' % layer)
            self.layer_inputs.append(layer)
        return self
        
        
    def get_output(self, layer):
        try:
            layer = self.layers[layer]
        except KeyError:
            print(list(self.layers.keys()))
            raise KeyError('Unknown layer name fed: %s' % layer)
        return layer
        
        
    def get_unique_name(self, prefix):
        id = sum(t.startswith(prefix) for t,_ in list(self.layers.items())) + 1
        return '%s_%d' % (prefix, id)